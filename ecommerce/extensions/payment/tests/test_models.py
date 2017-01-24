from oscar.test import factories

from ecommerce.extensions.payment.models import SdnCheckFailure
from ecommerce.tests.testcases import TestCase


class SdnCheckFailureTests(TestCase):
    def setUp(self):
        self.full_name = 'Darth Vader'
        self.country = 'Galactic Empire'
        self.sdn_check_response = {'description': 'One bad dude'}
        self.basket = factories.BasketFactory()

        self.failure_object = SdnCheckFailure.objects.create(
            full_name=self.full_name,
            country=self.country,
            sdn_check_response=self.sdn_check_response,
            basket=self.basket
        )

    def test_unicode_representation(self):
        """Verify the __unicode__ method returns the correct value."""
        expected = u'{full_name} [{country}] - basket [{basket_id}]'.format(
            full_name=self.full_name,
            country=self.country,
            basket_id=self.basket.id
        )

        self.assertEqual(unicode(self.failure_object), expected)
