# noinspection PyUnresolvedReferences
from oscar.apps.payment.exceptions import *


class ProcessorMisconfiguredError(Exception):
    """ Raised when a payment processor has invalid/missing settings. """
    pass
