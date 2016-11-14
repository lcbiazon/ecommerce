""" CyberSource payment processing. """
from __future__ import unicode_literals

import datetime
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from oscar.apps.payment.exceptions import UserCancelled, GatewayError, TransactionDeclined
from suds.client import Client
from suds.wsse import Security, UsernameToken

from ecommerce.edx_cybersource_plugin.constants import CYBERSOURCE_CARD_TYPE_MAP, ISO_8601_FORMAT
from ecommerce.edx_cybersource_plugin.exceptions import (
    InvalidSignatureError, InvalidCybersourceDecision, PartialAuthorizationError, PCIViolation
)
from ecommerce.edx_cybersource_plugin.helpers import sign, suds_response_to_dict
from ecommerce.edx_cybersource_plugin.transport import RequestsTransport
from ecommerce.edx_ecommerce_sdk.payment.processors import (
    AbstractClientPaymentProcessor, AbstractRedirectPaymentProcessor
)

logger = logging.getLogger(__name__)


class CybersourceMixin(object):
    DISPLAY_NAME = _('CyberSource')
    PCI_FIELDS = ('card_cvn', 'card_expiry_date', 'card_number', 'card_type',)

    def __init__(self, site):
        """
        Constructs a new instance of the CyberSource processor.

        Raises:
            KeyError: If no settings configured for this payment processor
            AttributeError: If LANGUAGE_CODE setting is not set.
        """

        super(CybersourceMixin, self).__init__(site)
        configuration = self.configuration
        self.soap_api_url = configuration['soap_api_url']
        self.merchant_id = configuration['merchant_id']
        self.transaction_key = configuration['transaction_key']
        self.profile_id = configuration['profile_id']
        self.access_key = configuration['access_key']
        self.secret_key = configuration['secret_key']
        self.payment_page_url = configuration['payment_page_url']
        self.send_level_2_3_details = configuration.get('send_level_2_3_details', True)
        self.language_code = settings.LANGUAGE_CODE

    @property
    def client_side_payment_url(self):
        return self.payment_page_url

    def get_transaction_parameters(self, basket, request=None, **kwargs):
        """
        Generate a dictionary of signed parameters CyberSource requires to complete a transaction.

        Arguments:
            basket (Basket): The basket of products being purchased.
            request (Request, optional): A Request object which could be used to construct an absolute URL; not
                used by this method.
            **kwargs: Additional parameters.

        Keyword Arguments:
            extra_parameters (dict): Additional signed parameters that should be included in the signature
                and returned dict. Note that these parameters will override any default values.

        Returns:
            dict: CyberSource-specific parameters required to complete a transaction, including a signature.
        """
        parameters = self._generate_parameters(basket, **kwargs)

        # Sign all fields
        parameters['signed_field_names'] = ','.join(sorted(parameters.keys()))
        parameters['signature'] = self._generate_signature(parameters)

        return parameters

    def _generate_parameters(self, basket, **kwargs):
        """ Generates the parameters dict.

        A signature is NOT included in the parameters.

         Arguments:
            basket (Basket): Basket from which the pricing and item details are pulled.
            **kwargs: Additional parameters to add to the generated dict.

         Returns:
             dict: Dictionary containing the payment parameters that should be sent to CyberSource.
        """
        site = basket.site

        parameters = {
            'access_key': self.access_key,
            'profile_id': self.profile_id,
            'transaction_uuid': uuid.uuid4().hex,
            'signed_field_names': '',
            'unsigned_field_names': '',
            'signed_date_time': datetime.datetime.utcnow().strftime(ISO_8601_FORMAT),
            'locale': self.language_code,
            'transaction_type': 'sale',
            'reference_number': basket.order_number,
            'amount': str(basket.total_incl_tax),
            'currency': basket.currency,
            'consumer_id': basket.owner.username,
        }

        # Level 2/3 details
        if self.send_level_2_3_details:
            parameters['amex_data_taa1'] = site.name
            parameters['purchasing_level'] = '3'
            parameters['line_item_count'] = basket.lines.count()
            # Note: This field (purchase order) is required for Visa;
            # but, is not actually used by us/exposed on the order form.
            parameters['user_po'] = 'BLANK'

            for index, line in enumerate(basket.lines.all()):
                parameters['item_{}_code'.format(index)] = line.product.get_product_class().slug
                parameters['item_{}_discount_amount '.format(index)] = str(line.discount_value)
                # Note: This indicates that the total_amount field below includes tax.
                parameters['item_{}_gross_net_indicator'.format(index)] = 'Y'
                parameters['item_{}_name'.format(index)] = line.product.title
                parameters['item_{}_quantity'.format(index)] = line.quantity
                parameters['item_{}_sku'.format(index)] = line.stockrecord.partner_sku
                parameters['item_{}_tax_amount'.format(index)] = str(line.line_tax)
                parameters['item_{}_tax_rate'.format(index)] = '0'
                parameters['item_{}_total_amount '.format(index)] = str(line.line_price_incl_tax_incl_discounts)
                # Note: Course seat is not a unit of measure. Use item (ITM).
                parameters['item_{}_unit_of_measure'.format(index)] = 'ITM'
                parameters['item_{}_unit_price'.format(index)] = str(line.unit_price_incl_tax)

        # Add the extra parameters
        parameters.update(kwargs.get('extra_parameters', {}))

        # Mitigate PCI compliance issues
        signed_field_names = parameters.keys()
        if any(pci_field in signed_field_names for pci_field in self.PCI_FIELDS):
            raise PCIViolation('One or more PCI-related fields is contained in the payment parameters. '
                               'This service is NOT PCI-compliant! Deactivate this service immediately!')

        return parameters

    def handle_processor_response(self, response, basket=None):
        """
        Handle a response (i.e., "merchant notification") from CyberSource.

        This method does the following:
            1. Verify the validity of the response.
            2. Create PaymentEvents and Sources for successful payments.

        Arguments:
            response (dict): Dictionary of parameters received from the payment processor.

        Keyword Arguments:
            basket (Basket): Basket being purchased via the payment processor.

        Raises:
            UserCancelled: Indicates the user cancelled payment.
            TransactionDeclined: Indicates the payment was declined by the processor.
            GatewayError: Indicates a general error on the part of the processor.
            InvalidCyberSourceDecision: Indicates an unknown decision value.
                Known values are ACCEPT, CANCEL, DECLINE, ERROR.
            PartialAuthorizationError: Indicates only a portion of the requested amount was authorized.
        """

        # Validate the signature
        if not self.is_signature_valid(response):
            raise InvalidSignatureError

        # Raise an exception for payments that were not accepted. Consuming code should be responsible for handling
        # and logging the exception.
        decision = response['decision'].lower()
        if decision != 'accept':
            exception = {
                'cancel': UserCancelled,
                'decline': TransactionDeclined,
                'error': GatewayError
            }.get(decision, InvalidCybersourceDecision)

            raise exception

        # Note: We do not currently support partial payments. Raise an exception if the authorized
        # amount differs from the requested amount.
        if response['auth_amount'] != response['req_amount']:
            raise PartialAuthorizationError

        # Create Source to track all transactions related to this processor and order
        currency = response['req_currency']
        total = Decimal(response['req_amount'])
        transaction_id = response['transaction_id']
        req_card_number = response['req_card_number']
        card_type = CYBERSOURCE_CARD_TYPE_MAP.get(response['req_card_type'])

        return transaction_id, total, currency, req_card_number, card_type

    def _generate_signature(self, parameters):
        """
        Sign the contents of the provided transaction parameters dictionary.

        This allows CyberSource to verify that the transaction parameters have not been tampered with
        during transit. The parameters dictionary should contain a key 'signed_field_names' which CyberSource
        uses to validate the signature. The message to be signed must contain parameter keys and values ordered
        in the same way they appear in 'signed_field_names'.

        We also use this signature to verify that the signature we get back from CybersourceSOP is valid for
        the parameters that they are giving to us.

        Arguments:
            parameters (dict): A dictionary of transaction parameters.

        Returns:
            unicode: the signature for the given parameters
        """
        keys = parameters['signed_field_names'].split(',')

        # Generate a comma-separated list of keys and values to be signed. CyberSource refers to this
        # as a 'Version 1' signature in their documentation.
        message = ','.join(['{key}={value}'.format(key=key, value=parameters.get(key)) for key in keys])

        return sign(message, self.secret_key)

    def is_signature_valid(self, response):
        """Returns a boolean indicating if the response's signature (indicating potential tampering) is valid."""
        return response and (self._generate_signature(response) == response.get('signature'))

    def issue_credit(self, order, reference_number, amount, currency):
        try:
            security = Security()
            token = UsernameToken(self.merchant_id, self.transaction_key)
            security.tokens.append(token)

            client = Client(self.soap_api_url, transport=RequestsTransport())
            client.set_options(wsse=security)

            credit_service = client.factory.create('ns0:CCCreditService')
            credit_service._run = 'true'  # pylint: disable=protected-access
            credit_service.captureRequestID = reference_number

            purchase_totals = client.factory.create('ns0:PurchaseTotals')
            purchase_totals.currency = currency
            purchase_totals.grandTotalAmount = str(amount)

            response = client.service.runTransaction(merchantID=self.merchant_id, merchantReferenceCode=order.number,
                                                     orderRequestToken=reference_number,
                                                     ccCreditService=credit_service,
                                                     purchaseTotals=purchase_totals)
            request_id = response.requestID
            self.record_processor_response(suds_response_to_dict(response), transaction_id=request_id,
                                           basket=order.basket)
        except:
            msg = 'An error occurred while attempting to issue a credit (via CyberSource) for order [{}].'.format(
                order.number)
            logger.exception(msg)
            raise GatewayError(msg)

        if response.decision == 'ACCEPT':
            return request_id
        else:
            msg = 'Failed to issue CyberSource credit for order [{order_number}].'.format(order_number=order.number)
            raise GatewayError(msg)


class CybersourceSOP(CybersourceMixin, AbstractClientPaymentProcessor):
    """
    CyberSource Secure Acceptance Silent Order POST

    For reference, see
    http://apps.cybersource.com/library/documentation/dev_guides/Secure_Acceptance_SOP/Secure_Acceptance_SOP.pdf.
    """
    NAME = 'cybersource_sop'


class CybersourceWebMobile(CybersourceMixin, AbstractRedirectPaymentProcessor):
    """
        CyberSource Secure Acceptance Web/Mobile

        For reference, see
        http://apps.cybersource.com/library/documentation/dev_guides/Secure_Acceptance_WM/Secure_Acceptance_WM.pdf.
        """
    NAME = 'cybersource_wm'
