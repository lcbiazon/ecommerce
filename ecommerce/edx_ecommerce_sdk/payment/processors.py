import abc

from django.conf import settings


class AbstractPaymentProcessor(object):  # pragma: no cover
    """Base payment processor class."""
    __metaclass__ = abc.ABCMeta

    # NOTE: Ensure that, if passed to a Django template, Django does not attempt to instantiate this class
    # or its children. Doing so without a Site object will cause issues.
    # See https://docs.djangoproject.com/en/1.8/ref/templates/api/#variables-and-lookups
    do_not_call_in_templates = True

    # Name of the processor.
    # This will be used programmatically to pull configuration, set CSS classes, etc. The value set should consist
    # of alphanumeric characters and underscores.
    NAME = None

    # Name of the processor as it should be displayed to users.
    # This value should be marked for translation, if appropriate.
    DISPLAY_NAME = None

    def __init__(self, site):
        super(AbstractPaymentProcessor, self).__init__()
        self.site = site

    @abc.abstractmethod
    def get_transaction_parameters(self, basket, request=None, **kwargs):
        """
        Generate a dictionary of signed parameters required for this processor to complete a transaction.

        Arguments:
            basket (Basket): The basket of products being purchased.
            request (Request, optional): A Request object which can be used to construct an absolute URL in
                cases where one is required.
            **kwargs: Additional parameters.

        Returns:
            dict: Payment processor-specific parameters required to complete a transaction.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def handle_processor_response(self, response, basket=None):
        """
        Handle a response from the payment processor.

        This method creates PaymentEvents and Sources for successful payments.

        Arguments:
            response (dict): Dictionary of parameters received from the payment processor

        Keyword Arguments:
            basket (Basket, optional): Basket whose contents have been purchased via the payment processor

        Returns:
            transaction_id (str): ID of the payment transaction at the payment processor.
            total (Decimal): Authorized payment amount.
            currency (str): Authorized payment currency.
            label (str): Label for the payment ``Source``. This is usually the card number.
            payment_method (str): Method via which payment was made. This is usually the type of credit card.
        """
        raise NotImplementedError

    @property
    def configuration(self):
        """
        Returns the configuration (set in Django settings) specific to this payment processor.

        Returns:
            dict: Payment processor configuration

        Raises:
            KeyError: If no settings found for this payment processor
        """
        partner_short_code = self.site.siteconfiguration.partner.short_code
        return settings.PAYMENT_PROCESSOR_CONFIG[partner_short_code.lower()][self.NAME.lower()]

    def record_processor_response(self, response, transaction_id=None, basket=None):
        """
        Save the processor's response to the database for auditing.

        Arguments:
            response (dict): Response received from the payment processor

        Keyword Arguments:
            transaction_id (string): Identifier for the transaction on the payment processor's servers
            basket (Basket): Basket associated with the payment event (e.g., being purchased)

        Return
            PaymentProcessorResponse
        """
        # TODO Log the data. The log handler will do the rest.
        # return PaymentProcessorResponse.objects.create(processor_name=self.NAME, transaction_id=transaction_id,
        #                                                response=response, basket=basket)

    @abc.abstractmethod
    def issue_credit(self, order, reference_number, amount, currency):
        """
        Issue a credit for the specified transaction.

        Arguments:
            order (Order): Order being refunded.
            reference_number (str): Reference number of the transaction being refunded.
            amount (Decimal): amount to be credited/refunded
            currency (string): currency of the amount to be credited

        Returns:
            str: Reference number of the *refund* transaction. Unless the payment processor groups related transactions,
             this will *NOT* be the same as the `reference_number` arg.
        """
        raise NotImplementedError

    def checkout_context(self, request):
        """
        Returns additional context that should be included on the checkout page for this payment processor.

        Arguments:
            request (Request): Request for which a response is being generated.

        Returns:
            dict
        """
        return {}


class AbstractClientPaymentProcessor(AbstractPaymentProcessor):
    """ Base class for client-side payment processors. """

    @abc.abstractproperty
    def client_side_payment_url(self):
        """
        Returns the URL to which payment data, collected directly from the payment page, should be posted.

        Returns:
            str
        """
        raise NotImplementedError


class AbstractRedirectPaymentProcessor(AbstractPaymentProcessor):
    """ Base class for payment processors that redirect to a third-party hosted payment payment page. """

    @abc.abstractproperty
    def redirect_url(self):
        """
        Returns the URL to which the user should be redirected to complete payment.

        Returns:
            str
        """
        raise NotImplementedError
