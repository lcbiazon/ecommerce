# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import hashlib

import ddt
import httpretty
import mock
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from oscar.core.loading import get_model
from oscar.test import factories

from ecommerce.core.tests.decorators import mock_course_catalog_api_client
from ecommerce.coupons.tests.mixins import CourseCatalogMockMixin, CouponMixin
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.tests.testcases import TestCase

Catalog = get_model('catalogue', 'Catalog')
ConditionalOffer = get_model('offer', 'ConditionalOffer')
Range = get_model('offer', 'Range')


@ddt.ddt
class RangeTests(CouponMixin, CourseCatalogTestMixin, CourseCatalogMockMixin, TestCase):
    def setUp(self):
        super(RangeTests, self).setUp()

        self.range = factories.RangeFactory()
        self.range_with_catalog = factories.RangeFactory()

        self.catalog = Catalog.objects.create(partner=self.partner)
        self.product = factories.create_product()

        self.range.add_product(self.product)
        self.range_with_catalog.catalog = self.catalog
        self.stock_record = factories.create_stockrecord(self.product, num_in_stock=2)
        self.catalog.stock_records.add(self.stock_record)

    def test_range_contains_product(self):
        """
        contains_product(product) should return Boolean value
        """
        self.assertTrue(self.range.contains_product(self.product))
        self.assertTrue(self.range_with_catalog.contains_product(self.product))

        not_in_range_product = factories.create_product()
        self.assertFalse(self.range.contains_product(not_in_range_product))
        self.assertFalse(self.range.contains_product(not_in_range_product))

    def test_range_number_of_products(self):
        """
        num_products() should return number of num_of_products
        """
        self.assertEqual(self.range.num_products(), 1)
        self.assertEqual(self.range_with_catalog.num_products(), 1)

    def test_range_all_products(self):
        """
        all_products() should return a list of products in range
        """
        self.assertIn(self.product, self.range.all_products())
        self.assertEqual(len(self.range.all_products()), 1)

        self.assertIn(self.product, self.range_with_catalog.all_products())
        self.assertEqual(len(self.range_with_catalog.all_products()), 1)

    def test_large_query(self):
        """Verify the range can store large queries."""
        large_query = """
            Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod
            tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam,
            quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo
            consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse
            cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat
            non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
        """
        self.range.catalog_query = large_query
        self.range.course_seat_types = 'verified'
        self.range.save()
        self.assertEqual(self.range.catalog_query, large_query)

    @mock.patch('ecommerce.core.url_utils.get_current_request', mock.Mock(return_value=None))
    def test_run_catalog_query_no_request(self):
        """
        run_course_query() should return status 400 response when no request is present.
        """
        with self.assertRaises(Exception):
            self.range.run_catalog_query(self.product)

    @httpretty.activate
    @mock_course_catalog_api_client
    def test_run_catalog_query(self):
        """
        run_course_query() should return True for included course run ID's.
        """
        course, seat = self.create_course_and_seat()
        self.mock_dynamic_catalog_contains_api(query='key:*', course_run_ids=[course.id])
        request = RequestFactory()
        request.site = self.site
        self.range.catalog_query = 'key:*'

        cache_key = hashlib.md5('catalog_query_contains [{}] [{}]'.format('key:*', seat.course_id)).hexdigest()
        cached_response = cache.get(cache_key)
        self.assertIsNone(cached_response)

        with mock.patch('ecommerce.core.url_utils.get_current_request', mock.Mock(return_value=request)):
            response = self.range.run_catalog_query(seat)
            self.assertTrue(response['course_runs'][course.id])
            cached_response = cache.get(cache_key)
            self.assertEqual(response, cached_response)

    @httpretty.activate
    @mock_course_catalog_api_client
    def test_query_range_contains_product(self):
        """
        contains_product() should return the correct boolean if a product is in it's range.
        """
        course, seat = self.create_course_and_seat()
        self.mock_dynamic_catalog_contains_api(query='key:*', course_run_ids=[course.id])

        false_response = self.range.contains_product(seat)
        self.assertFalse(false_response)

        self.range.catalog_query = 'key:*'
        self.range.course_seat_types = 'verified'
        response = self.range.contains_product(seat)
        self.assertTrue(response)

    @httpretty.activate
    @mock_course_catalog_api_client
    def test_query_range_all_products(self):
        """
        all_products() should return seats from the query.
        """
        course, seat = self.create_course_and_seat()
        self.assertEqual(len(self.range.all_products()), 1)
        self.assertFalse(seat in self.range.all_products())

        self.mock_dynamic_catalog_course_runs_api(query='key:*', course_run=course)
        self.range.catalog_query = 'key:*'
        self.range.course_seat_types = 'verified'
        self.assertEqual(len(self.range.all_products()), 0)

    @ddt.data(
        {'catalog_query': '*:*'},
        {'catalog_query': '', 'course_seat_types': ['verified']},
        {'course_seat_types': ['verified']},
    )
    def test_creating_range_with_wrong_data(self, data):
        """Verify creating range without catalog_query or catalog_seat_types raises ValidationError."""
        with self.assertRaises(ValidationError):
            Range.objects.create(**data)

    @ddt.data(
        {'catalog_query': '*:*'},
        {'catalog_query': '*:*', 'course_seat_types': ['verified']},
        {'course_seat_types': ['verified']}
    )
    def test_creating_range_with_catalog_and_dynamic_fields(self, data):
        """Verify creating range with catalog and dynamic fields set will raise exception."""
        data.update({'catalog': self.catalog})
        with self.assertRaises(ValidationError):
            Range.objects.create(**data)

    def test_creating_dynamic_range(self):
        """Verify creating range with catalog_query or catalog_seat_types creates range with those values."""
        data = {
            'catalog_query': 'id:testquery',
            'course_seat_types': 'verified,professional'
        }
        new_range = Range.objects.create(**data)
        self.assertEqual(new_range.catalog_query, data['catalog_query'])
        self.assertEqual(new_range.course_seat_types, data['course_seat_types'])
        self.assertEqual(new_range.catalog, None)

    @ddt.data(5, 'credit,verified', 'verified,not_allowed_value')
    def test_creating_range_with_wrong_course_seat_types(self, course_seat_types):
        """ Verify creating range with incorrect course seat types will raise exception. """
        data = {
            'catalog_query': '*:*',
            'course_seat_types': course_seat_types
        }
        with self.assertRaises(ValidationError):
            Range.objects.create(**data)

    @ddt.data('credit', 'professional', 'verified', 'professional,verified')
    def test_creating_range_with_course_seat_types(self, course_seat_types):
        """ Verify creating range with allowed course seat types values creates range. """
        data = {
            'catalog_query': '*:*',
            'course_seat_types': course_seat_types
        }
        _range = Range.objects.create(**data)
        self.assertEqual(_range.course_seat_types, course_seat_types)


@ddt.ddt
class ConditionalOfferTests(TestCase):
    """Tests for custom ConditionalOffer model."""
    def setUp(self):
        super(ConditionalOfferTests, self).setUp()

        self.valid_domain = 'example.com'
        self.valid_sub_domain = 'sub.example2.com'
        self.email_domains = '{domain1},{domain2}'.format(
            domain1=self.valid_domain,
            domain2=self.valid_sub_domain
        )
        self.product = factories.ProductFactory()
        _range = factories.RangeFactory(products=[self.product, ])

        self.offer = ConditionalOffer.objects.create(
            condition=factories.ConditionFactory(value=1, range=_range),
            benefit=factories.BenefitFactory(),
            email_domains=self.email_domains
        )

    def create_basket(self, email):
        """Helper method for creating a basket with specific owner."""
        user = self.create_user(email=email)
        basket = factories.BasketFactory(owner=user)
        basket.add_product(self.product, 1)
        return basket

    def test_condition_satisfied(self):
        """Verify a condition is satisfied."""
        self.assertEqual(self.offer.email_domains, self.email_domains)
        email = 'test@{domain}'.format(domain=self.valid_domain)
        basket = self.create_basket(email=email)
        self.assertTrue(self.offer.is_condition_satisfied(basket))

    def test_condition_not_satisfied(self):
        """Verify a condition is not satisfied."""
        self.assertEqual(self.offer.email_domains, self.email_domains)
        basket = self.create_basket(email='test@invalid.domain')
        self.assertFalse(self.offer.is_condition_satisfied(basket))

    def test_is_email_valid(self):
        """Verify method returns True for valid emails."""
        invalid_email = 'invalid@email.fake'
        self.assertFalse(self.offer.is_email_valid(invalid_email))

        valid_email = 'valid@{domain}'.format(domain=self.valid_sub_domain)
        self.assertTrue(self.offer.is_email_valid(valid_email))

        no_email_offer = factories.ConditionalOffer()
        self.assertTrue(no_email_offer.is_email_valid(invalid_email))

    def test_is_email_with_sub_domain_valid(self):
        """Verify method returns True for valid email domains with sub domain."""
        invalid_email = 'test@test{domain}'.format(domain=self.valid_sub_domain)  # test@testsub.example2.com
        self.assertFalse(self.offer.is_email_valid(invalid_email))

        valid_email = 'test@{domain}'.format(domain=self.valid_sub_domain)
        self.assertTrue(self.offer.is_email_valid(valid_email))

        valid_email_2 = 'test@sub2.{domain}'.format(domain=self.valid_domain)
        self.assertTrue(self.offer.is_email_valid(valid_email_2))

    @ddt.data(
        'domain.com', 'multi.it,domain.hr', 'sub.domain.net', '例如.com', 'val-id.例如', 'valid1.co例如',
        'valid-domain.com', 'çççç.рф', 'çç-ççç32.中国', 'ççç.ççç.இலங்கை'
    )
    def test_creating_offer_with_valid_email_domains(self, email_domains):
        """Verify creating ConditionalOffer with valid email domains."""
        offer = factories.ConditionalOfferFactory(email_domains=email_domains)
        self.assertEqual(offer.email_domains, email_domains)

    @ddt.data(
        '', 'noDot', 'spaceAfter.comma, domain.hr', 'nothingAfterDot.', '.nothingBeforeDot', 'space not.allowed',
        3, '-invalid.com', 'invalid', 'invalid-.com', 'invalid.c', 'valid.com,', 'invalid.photography1',
        'valid.com,invalid', 'valid.com,invalid-.com', 'valid.com,-invalid.com', 'in--valid.com',
        'in..valid.com', 'valid.com,invalid.c', 'invalid,valid.com', 'çççç.çç-çç', 'ççç.xn--ççççç', 'çççç.çç--çç.ççç'
    )
    def test_creating_offer_with_invalid_email_domains(self, email_domains):
        """Verify creating ConditionalOffer with invalid email domains raises validation error."""
        with self.assertRaises(ValidationError):
            factories.ConditionalOfferFactory(email_domains=email_domains)

    def test_creating_offer_with_valid_max_global_applications(self):
        """Verify creating ConditionalOffer with valid max global applications value."""
        offer = factories.ConditionalOfferFactory(max_global_applications=5)
        self.assertEqual(offer.max_global_applications, 5)

    @ddt.data(-2, 0, 'string', '')
    def test_creating_offer_with_invalid_max_global_applications(self, max_uses):
        """Verify creating ConditionalOffer with invalid max global applications value raises validation error."""
        with self.assertRaises(ValidationError):
            factories.ConditionalOfferFactory(max_global_applications=max_uses)
