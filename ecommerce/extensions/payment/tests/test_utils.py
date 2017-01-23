import json

import httpretty
from django.test import RequestFactory
from oscar.test.factories import BasketFactory

from ecommerce.extensions.payment.models import SDNCheckFailure
from ecommerce.extensions.payment.utils import clean_field_value, middle_truncate, sdn_check
from ecommerce.tests.factories import SiteConfigurationFactory
from ecommerce.tests.testcases import TestCase


class UtilsTests(TestCase):
    def test_truncation(self):
        """Verify that the truncation utility behaves as expected."""
        length = 10
        string = 'x' * length

        # Verify that the original string is returned when no truncation is necessary.
        self.assertEqual(string, middle_truncate(string, length))
        self.assertEqual(string, middle_truncate(string, length + 1))

        # Verify that truncation occurs when expected.
        self.assertEqual('xxx...xxx', middle_truncate(string, length - 1))
        self.assertEqual('xx...xx', middle_truncate(string, length - 2))

        self.assertRaises(ValueError, middle_truncate, string, 0)

    def test_clean_field_value(self):
        """ Verify the passed value is cleaned of specific special characters. """
        value = 'Some^text:\'test-value'
        self.assertEqual(clean_field_value(value), 'Sometexttest-value')


class SDNCheckTests(TestCase):
    """ Tests for the SDN check function. """
    def setUp(self):
        super(SDNCheckTests, self).setUp()
        self.request = RequestFactory()
        self.request.COOKIES = {}
        self.username = 'Dr. Evil'
        self.country = 'Evilland'
        self.request.user = self.create_user(full_name=self.username)
        site_configuration = SiteConfigurationFactory(
            partner__name='Tester',
            enable_sdn_check=True,
            sdn_api_url='http://sdn-test.fake',
            sdn_api_key='fake-key',
            sdn_api_list='SDN,TEST'
        )
        self.request.site = site_configuration.site

    def mock_sdn_response(self, response, status_code=200):
        """ Mock the SDN check API endpoint response. """
        httpretty.register_uri(
            httpretty.GET,
            self.request.site.siteconfiguration.sdn_check_url(self.username, self.country),
            status=status_code,
            body=json.dumps(response),
            content_type='application/json'
        )

    def assert_sdn_check_failure(self, basket, response):
        """ Assert an SDN check failure is logged and has the correct values. """
        self.assertEqual(SDNCheckFailure.objects.count(), 1)
        sdn_object = SDNCheckFailure.objects.first()
        self.assertEqual(sdn_object.full_name, self.username)
        self.assertEqual(sdn_object.country, self.country)
        self.assertEqual(sdn_object.sdn_check_response, response)
        self.assertEqual(sdn_object.basket, basket)

    @httpretty.activate
    def test_sdn_check_connection_error(self):
        """ Verify the check returns true in case of a connection error. """
        self.mock_sdn_response({}, status_code=400)
        BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailure.objects.count(), 0)
        self.assertTrue(sdn_check(self.request, self.username, self.country))

    @httpretty.activate
    def test_sdn_check_match(self):
        """ Verify the SDN check returns false for a match and records it. """
        sdn_response = {'total': 1}
        self.mock_sdn_response(sdn_response)
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailure.objects.count(), 0)
        self.assertFalse(sdn_check(self.request, self.username, self.country))

        self.assert_sdn_check_failure(basket, json.dumps(sdn_response))

    @httpretty.activate
    def test_sdn_check_pass(self):
        """ Verify the SDN check returns true if the user passed and no failure is saved. """
        self.mock_sdn_response({'total': 0})
        BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailure.objects.count(), 0)
        self.assertTrue(sdn_check(self.request, self.username, self.country))
        self.assertEqual(SDNCheckFailure.objects.count(), 0)
