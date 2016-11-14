from ecommerce.edx_ecommerce_sdk.payment.exceptions import *


class InvalidSignatureError(GatewayError):
    """The signature of the payment processor's response is invalid."""
    pass


class InvalidCybersourceDecision(GatewayError):
    """The decision returned by CyberSource was not recognized."""
    pass


class PartialAuthorizationError(PaymentError):
    """The amount authorized by the payment processor differs from the requested amount."""
    pass


class PCIViolation(PaymentError):
    """ Raised when a payment request violates PCI compliance.

    If we are raising this exception BAD things are happening, and the service MUST be taken offline IMMEDIATELY!
    """
    pass
