"""
Helper methods for enterprise entitlements.
"""
import logging

from oscar.core.loading import get_model

from ecommerce.coupons.views import voucher_is_valid
from ecommerce.enterprise.tmp import utils
from ecommerce.enterprise.utils import is_enterprise_feature_enabled


logger = logging.getLogger(__name__)
Voucher = get_model('voucher', 'Voucher')


def get_entitlement_voucher(request, product):
    """
    Returns entitlement voucher for the given product against an enterprise
    learner.

    Arguments:
        request (HttpRequest): request with voucher data
        product (Product): A product that has course_key as attribute (seat or
            bulk enrollment coupon)

    """
    if not is_enterprise_feature_enabled():
        return None

    vouchers = get_vouchers_for_learner(request.site, request.user)
    entitlement_voucher = get_available_voucher_for_product(request, product, vouchers)

    return entitlement_voucher

def get_vouchers_for_learner(site, learner):
    """
    Get vouchers against the list of all enterprise entitlements for the
    provided learner.

    Arguments:
        learner: (django.contrib.auth.User) django auth user
        site: (django.contrib.sites.Site) site instance

    """
    vouchers = []
    entitlements = get_entitlements_for_learner(site, learner)
    for entitlement in entitlements:
        try:
            voucher = Voucher.objects.get(id=entitlement['entitlement_id'])
            vouchers.append(voucher)
        except Voucher.DoesNotExist:
            logger.warning('No voucher found with the entitlement id %s', entitlement['entitlement_id'])

    return vouchers

def get_entitlements_for_learner(site, learner):
    """
    Get entitlements for the provided learner if the provided learner is
    affiliated with an enterprise.

    Arguments:
        learner: (django.contrib.auth.User) django auth user
        site: (django.contrib.sites.Site) site instance

    """
    entitlements = []
    try:
        enterprise_learner_data = get_enterprise_learner_data(site, learner)['results']
    except:  # pylint: disable=bare-except
        logger.exception(
            'Failed to retrieve enterprise info for the learner [%s]',
            learner.username
        )
        return entitlements

    if len(enterprise_learner_data) == 0:
        logger.info('Learner with username %s in not affiliated with any enterprise', learner.username)
        return entitlements

    entitlements = enterprise_learner_data[0]['enterprise_customer']['entitlements']
    return entitlements

@utils.dummy_data("enterprise_api_response_for_learner")
def get_enterprise_learner_data(site, learner):
    """
    Fetch data related to enterprise learners.

    Arguments:
        learner: (django.contrib.auth.User) django auth user
        site: (django.contrib.sites.Site) site instance

    """
    response = site.siteconfiguration.enterprise_api_client.enterprise-learner(learner.username).get()
    # TODO: Cache the response from enterprise API in case of 200 status

    return response

def get_available_voucher_for_product(request, product, vouchers):
    """
    Get first active entitlement from a list of vouchers for the given
    product.

    Arguments:
        product (Product): A product that has course_key as attribute (seat or
            bulk enrollment coupon)
        request (HttpRequest): request with voucher data
        vouchers: (List) List of voucher class objects for an enterprise

    """
    # TODO: Handle multiple entitlements/vouchers for an enterprise WRT some criterion

    for voucher in vouchers:
        is_valid_voucher, __ = voucher_is_valid(voucher, [product], request)
        if is_valid_voucher:
            return voucher
