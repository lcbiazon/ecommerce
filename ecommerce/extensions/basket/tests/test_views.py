import datetime
import hashlib
import json
import urllib

import ddt
import httpretty
import pytz
from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage  # Messages don't work without fallback
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.test import override_settings, RequestFactory
from django.utils.translation import ugettext_lazy as _
from oscar.apps.basket.forms import BasketVoucherForm
from oscar.core.loading import get_class, get_model
from oscar.test import newfactories as factories
from requests.exceptions import ConnectionError, Timeout
from slumber.exceptions import SlumberBaseException
from testfixtures import LogCapture
from waffle.testutils import override_flag

from ecommerce.core.constants import ENROLLMENT_CODE_PRODUCT_CLASS_NAME, ENROLLMENT_CODE_SWITCH
from ecommerce.core.exceptions import SiteConfigurationError
from ecommerce.core.tests import toggle_switch
from ecommerce.core.tests.decorators import mock_course_catalog_api_client
from ecommerce.core.url_utils import get_lms_enrollment_api_url
from ecommerce.core.url_utils import get_lms_url
from ecommerce.coupons.tests.mixins import CouponMixin, CourseCatalogMockMixin
from ecommerce.courses.tests.factories import CourseFactory
from ecommerce.extensions.basket.utils import get_basket_switch_data
from ecommerce.extensions.basket.views import VoucherAddMessagesView
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.extensions.offer.utils import format_benefit_value
from ecommerce.extensions.payment.constants import CLIENT_SIDE_CHECKOUT_FLAG_NAME
from ecommerce.extensions.payment.forms import PaymentForm
from ecommerce.extensions.payment.processors.cybersource import Cybersource
from ecommerce.extensions.payment.tests.processors import DummyProcessor
from ecommerce.extensions.test.factories import prepare_voucher
from ecommerce.tests.factories import StockRecordFactory
from ecommerce.tests.mixins import ApiMockMixin, LmsApiMockMixin
from ecommerce.tests.testcases import TestCase

Applicator = get_class('offer.utils', 'Applicator')
Basket = get_model('basket', 'Basket')
Benefit = get_model('offer', 'Benefit')
Catalog = get_model('catalogue', 'Catalog')
Product = get_model('catalogue', 'Product')
ProductAttribute = get_model('catalogue', 'ProductAttribute')
Selector = get_class('partner.strategy', 'Selector')
StockRecord = get_model('partner', 'StockRecord')
Voucher = get_model('voucher', 'Voucher')
VoucherApplication = get_model('voucher', 'VoucherApplication')

COUPON_CODE = 'COUPONTEST'


@ddt.ddt
class BasketSingleItemViewTests(CouponMixin, CourseCatalogTestMixin, CourseCatalogMockMixin, LmsApiMockMixin, TestCase):
    """ BasketSingleItemView view tests. """
    path = reverse('basket:single-item')

    def setUp(self):
        super(BasketSingleItemViewTests, self).setUp()
        self.user = self.create_user()
        self.client.login(username=self.user.username, password=self.password)

        self.course = CourseFactory()
        self.course.create_or_update_seat('verified', True, 50, self.partner)
        product = self.course.create_or_update_seat('verified', False, 0, self.partner)
        self.stock_record = StockRecordFactory(product=product, partner=self.partner)
        self.catalog = Catalog.objects.create(partner=self.partner)
        self.catalog.stock_records.add(self.stock_record)

    def mock_enrollment_api_success_enrolled(self, course_id, mode='audit'):
        """
        Returns a successful Enrollment API response indicating self.user is enrolled in the specified course mode.
        """
        self.assertTrue(httpretty.is_enabled())
        url = '{host}/{username},{course_id}'.format(
            host=get_lms_enrollment_api_url(),
            username=self.user.username,
            course_id=course_id
        )
        json_body = json.dumps({'mode': mode, 'is_active': True})
        httpretty.register_uri(httpretty.GET, url, body=json_body, content_type='application/json')

    def mock_enrollment_api_success_unenrolled(self, course_id, mode='audit'):
        """
        Returns a successful Enrollment API response indicating self.user is unenrolled in the specified course mode.
        """
        self.assertTrue(httpretty.is_enabled())
        url = '{host}/{username},{course_id}'.format(
            host=get_lms_enrollment_api_url(),
            username=self.user.username,
            course_id=course_id
        )
        json_body = json.dumps({'mode': mode, 'is_active': False})
        httpretty.register_uri(httpretty.GET, url, body=json_body, content_type='application/json')

    def test_login_required(self):
        """ The view should redirect to login page if the user is not logged in. """
        self.client.logout()
        response = self.client.get(self.path)
        testserver_login_url = self.get_full_url(reverse('login'))
        expected_url = '{path}?next={basket_path}'.format(path=testserver_login_url, basket_path=self.path)
        self.assertRedirects(response, expected_url, target_status_code=302)

    def test_missing_sku(self):
        """ The view should return HTTP 400 if no SKU is provided. """
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, 'No SKU provided.')

    def test_missing_product(self):
        """ The view should return HTTP 400 if SKU has no associated product. """
        sku = 'NONEXISTING'
        expected_content = 'SKU [{}] does not exist.'.format(sku)
        url = '{path}?sku={sku}'.format(path=self.path, sku=sku)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, expected_content)

    @httpretty.activate
    def test_unavailable_product(self):
        """ The view should return HTTP 400 if the product is not available for purchase. """
        self.mock_enrollment_api_success_enrolled(self.course.id)
        product = self.stock_record.product
        product.expires = pytz.utc.localize(datetime.datetime.min)
        product.save()
        self.assertFalse(Selector().strategy().fetch_for_product(product).availability.is_available_to_buy)

        expected_content = 'Product [{}] not available to buy.'.format(product.title)
        url = '{path}?sku={sku}'.format(path=self.path, sku=self.stock_record.partner_sku)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, expected_content)

    @httpretty.activate
    def test_redirect_to_basket_summary(self):
        """
        Verify the view redirects to the basket summary page, and that the user's basket is prepared for checkout.
        """
        self.mock_enrollment_api_success_enrolled(self.course.id)
        self.create_coupon(catalog=self.catalog, code=COUPON_CODE, benefit_value=5)

        self.mock_dynamic_catalog_course_runs_api(course_run=self.course)
        url = '{path}?sku={sku}&code={code}'.format(path=self.path, sku=self.stock_record.partner_sku,
                                                    code=COUPON_CODE)
        response = self.client.get(url)
        expected_url = self.get_full_url(reverse('basket:summary'))
        self.assertRedirects(response, expected_url, status_code=303)

        basket = Basket.objects.get(owner=self.user, site=self.site)
        self.assertEqual(basket.status, Basket.OPEN)
        self.assertEqual(basket.lines.count(), 1)
        self.assertTrue(basket.contains_a_voucher)
        self.assertEqual(basket.lines.first().product, self.stock_record.product)

    @httpretty.activate
    @ddt.data(('verified', False), ('professional', True), ('no-id-professional', False))
    @ddt.unpack
    def test_enrolled_verified_student(self, mode, id_verification):
        """
        Verify the view return HTTP 400 if the student is already enrolled as verified student in the course
        (The Enrollment API call being used returns an active enrollment record in this case)
        """
        course = CourseFactory()
        self.mock_enrollment_api_success_enrolled(course.id, mode=mode)
        product = course.create_or_update_seat(mode, id_verification, 0, self.partner)
        stock_record = StockRecordFactory(product=product, partner=self.partner)
        catalog = Catalog.objects.create(partner=self.partner)
        catalog.stock_records.add(stock_record)
        self.create_coupon(catalog=catalog, code=COUPON_CODE, benefit_value=5)

        url = '{path}?sku={sku}&code={code}'.format(path=self.path, sku=stock_record.partner_sku, code=COUPON_CODE)
        expected_content = 'You are already enrolled in {product}.'.format(product=product.course.name)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, expected_content)

    @httpretty.activate
    @ddt.data(('verified', False), ('professional', True), ('no-id-professional', False))
    @ddt.unpack
    def test_unenrolled_verified_student(self, mode, id_verification):
        """
        Verify the view return HTTP 303 if the student is unenrolled as verified student in the course
        (The Enrollment API call being used returns an inactive enrollment record in this case)
        """
        course = CourseFactory()
        self.mock_enrollment_api_success_unenrolled(course.id, mode=mode)
        product = course.create_or_update_seat(mode, id_verification, 0, self.partner)
        stock_record = StockRecordFactory(product=product, partner=self.partner)
        catalog = Catalog.objects.create(partner=self.partner)
        catalog.stock_records.add(stock_record)
        sku = stock_record.partner_sku

        url = '{path}?sku={sku}'.format(path=self.path, sku=sku)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.reason_phrase, "SEE OTHER")
        self.assertEqual(response.wsgi_request.path_info, '/basket/single-item/')
        self.assertEqual(response.wsgi_request.GET['sku'], sku)

    @httpretty.activate
    @ddt.data(ConnectionError, SlumberBaseException, Timeout)
    def test_enrollment_api_failure(self, error):
        """
        Verify the view returns HTTP status 400 if the Enrollment API is not available.
        """
        self.request.user = self.user
        self.mock_enrollment_api_error(self.request, self.user, self.course.id, error)
        self.create_coupon(catalog=self.catalog, code=COUPON_CODE, benefit_value=5)
        url = '{path}?sku={sku}&code={code}'.format(path=self.path, sku=self.stock_record.partner_sku, code=COUPON_CODE)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)


@httpretty.activate
@ddt.ddt
class BasketSummaryViewTests(CourseCatalogTestMixin, CourseCatalogMockMixin, LmsApiMockMixin, ApiMockMixin, TestCase):
    """ BasketSummaryView basket view tests. """
    path = reverse('basket:summary')

    def setUp(self):
        super(BasketSummaryViewTests, self).setUp()
        self.user = self.create_user()
        self.client.login(username=self.user.username, password=self.password)
        self.course = CourseFactory(name='BasketSummaryTest')
        site_configuration = self.site.siteconfiguration

        old_payment_processors = site_configuration.payment_processors
        site_configuration.payment_processors = DummyProcessor.NAME
        site_configuration.save()

        def reset_site_config():
            """ Reset method - resets site_config to pre-test state """
            site_configuration.payment_processors = old_payment_processors
            site_configuration.save()

        self.addCleanup(reset_site_config)

        toggle_switch(settings.PAYMENT_PROCESSOR_SWITCH_PREFIX + DummyProcessor.NAME, True)

    def create_basket_and_add_product(self, product):
        basket = factories.BasketFactory(owner=self.user, site=self.site)
        basket.add_product(product, 1)
        return basket

    def create_seat(self, course, seat_price=100, cert_type='verified'):
        return course.create_or_update_seat(cert_type, True, seat_price, self.partner)

    def create_and_apply_benefit_to_basket(self, basket, product, benefit_type, benefit_value):
        _range = factories.RangeFactory(products=[product, ])
        voucher, __ = prepare_voucher(_range=_range, benefit_type=benefit_type, benefit_value=benefit_value)
        basket.vouchers.add(voucher)
        Applicator().apply(basket)

    @ddt.data(ConnectionError, SlumberBaseException, Timeout)
    def test_course_api_failure(self, error):
        """ Verify a connection error and timeout are logged when they happen. """
        seat = self.create_seat(self.course)
        basket = self.create_basket_and_add_product(seat)
        self.assertEqual(basket.lines.count(), 1)

        logger_name = 'ecommerce.extensions.basket.views'
        self.mock_api_error(
            error=error,
            url=get_lms_url('api/courses/v1/courses/{}/'.format(self.course.id))
        )

        with LogCapture(logger_name) as l:
            response = self.client.get(self.path)
            self.assertEqual(response.status_code, 200)
            l.check(
                (
                    logger_name, 'ERROR',
                    u'Failed to retrieve data from Catalog Service for course [{}].'.format(self.course.id)
                )
            )

    def test_non_seat_product(self):
        """Verify the basket accepts non-seat product types."""
        title = 'Test Product 123'
        description = 'All hail the test product.'
        product = factories.ProductFactory(title=title, description=description)
        self.create_basket_and_add_product(product)

        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        line_data = response.context['formset_lines_data'][0][1]
        self.assertEqual(line_data['product_title'], title)
        self.assertEqual(line_data['product_description'], description)

    def test_enrollment_code_seat_type(self):
        """Verify the correct seat type attribute is retrieved."""
        course = CourseFactory()
        toggle_switch(ENROLLMENT_CODE_SWITCH, True)
        course.create_or_update_seat('verified', False, 10, self.partner, create_enrollment_code=True)
        enrollment_code = Product.objects.get(product_class__name=ENROLLMENT_CODE_PRODUCT_CLASS_NAME)
        self.create_basket_and_add_product(enrollment_code)
        self.mock_dynamic_catalog_course_runs_api(course_run=course)

        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_voucher_form'])

        # Enable enrollment codes
        self.site.siteconfiguration.enable_enrollment_codes = True
        self.site.siteconfiguration.save()

        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_voucher_form'])
        line_data = response.context['formset_lines_data'][0][1]
        self.assertEqual(line_data['seat_type'], _(enrollment_code.attr.seat_type.capitalize()))

    def test_no_switch_link(self):
        """Verify response does not contain variables for the switch link if seat does not have an EC."""
        toggle_switch(ENROLLMENT_CODE_SWITCH, True)
        ec_course = CourseFactory()
        no_ec_course = CourseFactory()
        seat_without_ec = no_ec_course.create_or_update_seat('verified', False, 10, self.partner)
        seat_with_ec = ec_course.create_or_update_seat('verified', False, 10, self.partner, create_enrollment_code=True)
        self.create_basket_and_add_product(seat_without_ec)
        self.mock_dynamic_catalog_course_runs_api(course_run=no_ec_course)

        response = self.client.get(self.path)
        self.assertFalse(response.context['switch_link_text'])
        self.assertFalse(response.context['partner_sku'])

        # Enable enrollment codes
        self.site.siteconfiguration.enable_enrollment_codes = True
        self.site.siteconfiguration.save()

        Basket.objects.all().delete()
        self.create_basket_and_add_product(seat_with_ec)
        self.mock_dynamic_catalog_course_runs_api(course_run=ec_course)

        response = self.client.get(self.path)
        enrollment_code = Product.objects.get(product_class__name=ENROLLMENT_CODE_PRODUCT_CLASS_NAME)
        enrollment_code_stockrecord = StockRecord.objects.get(product=enrollment_code)
        self.assertTrue(response.context['switch_link_text'])
        self.assertEqual(response.context['partner_sku'], enrollment_code_stockrecord.partner_sku)

    def test_basket_switch_data(self):
        """Verify the correct basket switch data (single vs. multi quantity) is retrieved."""
        course = CourseFactory()
        toggle_switch(ENROLLMENT_CODE_SWITCH, True)

        seat = course.create_or_update_seat('verified', False, 10, self.partner, create_enrollment_code=True)
        seat_sku = StockRecord.objects.get(product=seat).partner_sku
        enrollment_code = Product.objects.get(product_class__name=ENROLLMENT_CODE_PRODUCT_CLASS_NAME)
        ec_sku = StockRecord.objects.get(product=enrollment_code).partner_sku

        __, partner_sku = get_basket_switch_data(seat)
        self.assertEqual(partner_sku, ec_sku)
        __, partner_sku = get_basket_switch_data(enrollment_code)
        self.assertEqual(partner_sku, seat_sku)

    @ddt.data(
        (Benefit.PERCENTAGE, 100),
        (Benefit.PERCENTAGE, 50),
        (Benefit.FIXED, 50)
    )
    @ddt.unpack
    @mock_course_catalog_api_client
    @override_settings(PAYMENT_PROCESSORS=['ecommerce.extensions.payment.tests.processors.DummyProcessor'])
    def test_response_success(self, benefit_type, benefit_value):
        """ Verify a successful response is returned. """
        seat = self.create_seat(self.course, 500)
        basket = self.create_basket_and_add_product(seat)
        self.create_and_apply_benefit_to_basket(basket, seat, benefit_type, benefit_value)

        self.assertEqual(basket.lines.count(), 1)
        self.mock_dynamic_catalog_single_course_runs_api(self.course)

        benefit, __ = Benefit.objects.get_or_create(type=benefit_type, value=benefit_value)

        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['formset_lines_data']), 1)
        line_data = response.context['formset_lines_data'][0][1]
        self.assertEqual(line_data['benefit_value'], format_benefit_value(benefit))
        self.assertEqual(line_data['seat_type'], _(seat.attr.certificate_type.capitalize()))
        self.assertEqual(line_data['product_title'], self.course.name)
        self.assertFalse(line_data['enrollment_code'])
        self.assertEqual(response.context['payment_processors'][0].NAME, DummyProcessor.NAME)

    def assert_emtpy_basket(self):
        """ Assert that the basket is empty on visiting the basket summary page. """
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['formset_lines_data'], [])
        self.assertEqual(response.context['total_benefit'], None)

    def test_no_basket_response(self):
        """ Verify there are no form, line and benefit data in the context for a non-existing basket. """
        self.assert_emtpy_basket()

    def test_line_item_discount_data(self):
        """ Verify that line item has correct discount data. """
        self.mock_dynamic_catalog_course_runs_api(course_run=self.course)
        seat = self.create_seat(self.course)
        basket = self.create_basket_and_add_product(seat)
        self.create_and_apply_benefit_to_basket(basket, seat, Benefit.PERCENTAGE, 50)

        course_without_benefit = CourseFactory()
        seat_without_benefit = self.create_seat(course_without_benefit)
        basket.add_product(seat_without_benefit, 1)

        response = self.client.get(self.path)
        lines = response.context['formset_lines_data']
        self.assertEqual(lines[0][1]['benefit_value'], '50%')
        self.assertEqual(lines[1][1]['benefit_value'], None)

    @mock_course_catalog_api_client
    def test_cached_course(self):
        """ Verify that the course info is cached. """
        seat = self.create_seat(self.course, 50)
        basket = self.create_basket_and_add_product(seat)
        self.assertEqual(basket.lines.count(), 1)
        self.mock_dynamic_catalog_single_course_runs_api(self.course)

        cache_key = 'courses_api_detail_{}{}'.format(self.course.id, self.site.siteconfiguration.partner.short_code)
        cache_key = hashlib.md5(cache_key).hexdigest()
        cached_course_before = cache.get(cache_key)
        self.assertIsNone(cached_course_before)

        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        cached_course_after = cache.get(cache_key)
        self.assertEqual(cached_course_after['title'], self.course.name)

    @ddt.data({
        'course': 'edX+DemoX',
        'short_description': None,
        'title': 'Junk',
        'start': '2013-02-05T05:00:00Z',
    }, {
        'course': 'edX+DemoX',
        'short_description': None,
    })
    @mock_course_catalog_api_client
    def test_empty_catalog_api_response(self, course_info):
        """ Check to see if we can handle empty response from the catalog api """
        seat = self.create_seat(self.course)
        self.create_basket_and_add_product(seat)
        self.mock_dynamic_catalog_single_course_runs_api(self.course, course_info)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        line_data = response.context['formset_lines_data'][0][1]
        self.assertEqual(line_data.get('image_url'), '')
        self.assertEqual(line_data.get('course_short_description'), None)

    @ddt.data(
        ('verified', True),
        ('credit', False)
    )
    @ddt.unpack
    def test_verification_message(self, cert_type, ver_req):
        """ Verify the variable for verification requirement is False for credit seats. """
        seat = self.create_seat(self.course, cert_type=cert_type)
        self.create_basket_and_add_product(seat)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['display_verification_message'], ver_req)

    def test_verification_attribute_missing(self):
        """ Verify the variable for verification requirement is False when the attribute is missing. """
        seat = self.create_seat(self.course)
        ProductAttribute.objects.filter(name='id_verification_required').delete()
        self.create_basket_and_add_product(seat)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['display_verification_message'], False)

    @override_flag(CLIENT_SIDE_CHECKOUT_FLAG_NAME, active=True)
    def test_client_side_checkout(self):
        """ Verify the view returns the data necessary to initiate client-side checkout. """
        seat = self.create_seat(self.course)
        basket = self.create_basket_and_add_product(seat)

        response = self.client.get(self.get_full_url(self.path))
        self.assertEqual(response.status_code, 200)
        expected = {
            'enable_client_side_checkout': True,
            'payment_url': Cybersource(self.site).client_side_payment_url,
        }
        self.assertDictContainsSubset(expected, response.context)

        payment_form = response.context['payment_form']
        self.assertIsInstance(payment_form, PaymentForm)
        self.assertEqual(payment_form.initial['basket'], basket)

    @override_flag(CLIENT_SIDE_CHECKOUT_FLAG_NAME, active=True)
    def test_client_side_checkout_with_invalid_configuration(self):
        """ Verify an error is raised if a payment processor is defined as the client-side processor,
        but is not active in the system."""
        self.site.siteconfiguration.client_side_payment_processor = 'blah'
        self.site.siteconfiguration.save()

        seat = self.create_seat(self.course)
        self.create_basket_and_add_product(seat)

        with self.assertRaises(SiteConfigurationError):
            self.client.get(self.get_full_url(self.path))

    def test_login_required_basket_summary(self):
        """ The view should redirect to the login page if the user is not logged in. """
        self.client.logout()
        response = self.client.get(self.path)
        testserver_login_url = self.get_full_url(reverse(settings.LOGIN_URL))
        expected_url = '{path}?next={next}'.format(path=testserver_login_url, next=urllib.quote(self.path))
        self.assertRedirects(response, expected_url, target_status_code=302)


class VoucherAddMessagesViewTests(TestCase):
    """ VoucherAddMessagesView view tests. """

    def setUp(self):
        super(VoucherAddMessagesViewTests, self).setUp()
        self.user = self.create_user()
        self.client.login(username=self.user.username, password=self.password)
        self.basket = factories.BasketFactory(owner=self.user, site=self.site)

        self.request = RequestFactory().request()
        # Fallback storage is needed in tests with messages
        setattr(self.request, 'session', 'session')
        messages = FallbackStorage(self.request)
        setattr(self.request, '_messages', messages)
        self.request.user = self.user

        self.voucher_add_view = VoucherAddMessagesView()
        self.form = BasketVoucherForm()
        self.form.cleaned_data = {'code': COUPON_CODE}

    def get_error_message_from_request(self):
        return list(get_messages(self.request))[-1].message

    def assertMessage(self, message):
        self.request.basket = self.basket
        self.voucher_add_view.request = self.request
        self.voucher_add_view.form_valid(self.form)
        request_message = self.get_error_message_from_request()
        self.assertEqual(request_message, message)

    def test_no_voucher_error_msg(self):
        """ Verify correct error message is returned when voucher can't be found. """
        self.assertMessage(_("Coupon code '{code}' does not exist.").format(code=COUPON_CODE))

    def test_voucher_already_in_basket_error_msg(self):
        """ Verify correct error message is returned when voucher already in basket. """
        voucher = factories.VoucherFactory(code=COUPON_CODE)
        self.basket.vouchers.add(voucher)
        self.assertMessage(_("You have already added coupon code '{code}' to your basket.").format(code=COUPON_CODE))

    def test_voucher_expired_error_msg(self):
        """ Verify correct error message is returned when voucher has expired. """
        end_datetime = datetime.datetime.now() - datetime.timedelta(days=1)
        start_datetime = datetime.datetime.now() - datetime.timedelta(days=2)
        factories.VoucherFactory(code=COUPON_CODE, end_datetime=end_datetime, start_datetime=start_datetime)
        self.assertMessage(_("Coupon code '{code}' has expired.").format(code=COUPON_CODE))

    def test_voucher_added_to_basket_msg(self):
        """ Verify correct message is returned when voucher is added to basket. """
        __, product = prepare_voucher(code=COUPON_CODE)
        self.basket.add_product(product)
        self.assertMessage(_("Coupon code '{code}' added to basket.").format(code=COUPON_CODE))

    def test_voucher_has_no_discount_error_msg(self):
        """ Verify correct error message is returned when voucher has no discount. """
        factories.VoucherFactory(code=COUPON_CODE)
        self.assertMessage(_("Your basket does not qualify for a coupon code discount."))

    def test_voucher_used_error_msg(self):
        """ Verify correct error message is returned when voucher has been used (Single use). """
        voucher, __ = prepare_voucher(code=COUPON_CODE)
        order = factories.OrderFactory()
        VoucherApplication.objects.create(voucher=voucher, user=self.user, order=order)
        self.assertMessage(_("Coupon code '{code}' has already been redeemed.").format(code=COUPON_CODE))

    def test_voucher_else_error_msg(self):
        """ Verify correct error message is returned when error case in not covered. """
        voucher, __ = prepare_voucher(code=COUPON_CODE, usage=Voucher.ONCE_PER_CUSTOMER)
        order = factories.OrderFactory()
        VoucherApplication.objects.create(voucher=voucher, user=self.user, order=order)
        self.assertMessage(_("Coupon code '{code}' is invalid.").format(code=COUPON_CODE))
