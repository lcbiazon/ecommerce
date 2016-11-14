from __future__ import unicode_literals

from ecommerce.edx_cybersource_plugin import helpers
from ecommerce.tests.testcases import TestCase


class HelperTests(TestCase):
    def test_sign(self):
        """ Verify the function returns a valid HMAC SHA-256 signature. """
        message = 'This is a super-secret message!'
        secret = 'password'
        expected = 'qU4fRskS/R9yZx/yPq62sFGOUzX0GSUtmeI6bPVsqao='
        self.assertEqual(helpers.sign(message, secret), expected)
