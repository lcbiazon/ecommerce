import re

import json
import logging

import requests
from django.utils.translation import ugettext_lazy as _
from oscar.core.loading import get_model
from rest_framework import status

from ecommerce.extensions.payment.models import SDNCheckFailure

logger = logging.getLogger(__name__)
Basket = get_model('basket', 'Basket')


def middle_truncate(string, chars):
    """Truncate the provided string, if necessary.

    Cuts excess characters from the middle of the string and replaces
    them with a string indicating that truncation has occurred.

    Arguments:
        string (unicode or str): The string to be truncated.
        chars (int): The character limit for the truncated string.

    Returns:
        Unicode: The truncated string, of length less than or equal to `chars`.
            If no truncation was required, the original string is returned.

    Raises:
        ValueError: If the provided character limit is less than the length of
            the truncation indicator.
    """
    if len(string) <= chars:
        return string

    # Translators: This is a string placed in the middle of a truncated string
    # to indicate that truncation has occurred. For example, if a title may only
    # be at most 11 characters long, "A Very Long Title" (17 characters) would be
    # truncated to "A Ve...itle".
    indicator = _('...')

    indicator_length = len(indicator)
    if chars < indicator_length:
        raise ValueError

    slice_size = (chars - indicator_length) / 2
    start, end = string[:slice_size], string[-slice_size:]
    truncated = u'{start}{indicator}{end}'.format(start=start, indicator=indicator, end=end)

    return truncated


def clean_field_value(value):
    """Strip the value of any special characters.

    Currently strips caret(^), colon(:) and quote(" ') characters from the value.

    Args:
        value (str): The original value.

    Returns:
        A cleaned string.
    """
    return re.sub(r'[\^:"\']', '', value)


def sdn_check(request, full_name, address, country):
    """
    Call SDN check API to check if the user is on the US Treasury Department OFAC list.

    Arguments:
        request (Request): The request object made to the view.
        full_name(str): Full name of the user who is checked.
        address(str): User's address.
        country(str): User's country.

    Returns:
        result (Bool): Whether or not there is a match.
    """
    site_config = request.site.siteconfiguration
    basket = Basket.get_basket(request.user, request.site)
    response = requests.get(site_config.sdn_check_url(full_name, address, country))

    if response.status_code != status.HTTP_200_OK:
        logger.info(
            'Unable to connect to US Treasury SDN API for basket [%d]. Status code [%d] with message: %s',
            basket.id, response.status_code, response.content
        )
        return True
    elif json.loads(response.content)['total'] == 0:
        return True
    else:
        SDNCheckFailure.objects.create(
            full_name=full_name,
            address=address,
            country=country,
            sdn_check_response=response.content,
            basket=basket
        )
        logger.info('SDN check failed for user [%s] on basket id [%d]', full_name, basket.id)
        return False
