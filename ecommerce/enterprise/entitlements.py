"""
Helper methods for getting site based enterprise entitlements against the
learners.

Enterprise learners can get coupons, offered by their respective Enterprise
customers with which they are affiliated. The coupon product id's for the
enterprise entitlements are provided by the Enterprise Service on the bases
the learner's enterprise eligibility criterion.
"""
import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from oscar.core.loading import get_model
from requests.exceptions import ConnectionError, Timeout
from slumber.exceptions import SlumberBaseException

from ecommerce.coupons.utils import get_catalog_course_runs
from ecommerce.coupons.views import voucher_is_valid
from ecommerce.courses.utils import get_course_catalogs
from ecommerce.enterprise.utils import is_enterprise_feature_enabled
from ecommerce.extensions.api.serializers import retrieve_voucher


logger = logging.getLogger(__name__)
Product = get_model('catalogue', 'Product')
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
            coupon_product = Product.objects.filter(product_class__name='Coupon').get(id=entitlement['entitlement_id'])
        except Product.DoesNotExist:
            logger.exception(
                'There was an error getting coupon product with the entitlement id %s',
                entitlement['entitlement_id']
            )
            return []

        entitlement_voucher = retrieve_voucher(coupon_product)
        vouchers.append(entitlement_voucher)

    return vouchers


def get_entitlements_for_learner(site, learner):
    """
    Get entitlements for the provided learner if the provided learner is
    affiliated with an enterprise.

    Arguments:
        learner: (django.contrib.auth.User) django auth user
        site: (django.contrib.sites.Site) site instance

    """
    try:
        enterprise_learner_data = get_enterprise_learner_data(site, learner)['results']
    except (ConnectionError, SlumberBaseException, Timeout, KeyError):
        logger.exception(
            'Failed to retrieve enterprise info for the learner [%s]',
            learner.username
        )
        return []

    if len(enterprise_learner_data) == 0:
        logger.info('Learner with username %s in not affiliated with any enterprise', learner.username)
        return []

    try:
        entitlements = enterprise_learner_data[0]['enterprise_customer']['entitlements']
    except KeyError:
        logger.info('Invalid structure for enterprise learner API response')
        return []

    return entitlements


def get_enterprise_learner_data(site, learner):
    """
    Fetch information related to enterprise and its entitlements according to
    the eligibility criterion for the provided learners from the Enterprise
    Service.

    Example:
        get_enterprise_learner_data(site, learner)

    Arguments:
        learner: (django.contrib.auth.User) django auth user
        site: (django.contrib.sites.Site) site instance

    Returns:
        dict: {
            "enterprise_api_response_for_learner": {
                "count": 1,
                "num_pages": 1,
                "current_page": 1,
                "results": [
                    {
                        "enterprise_customer": {
                            "uuid": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                            "name": "TestShib",
                            "catalog": 2,
                            "active": true,
                            "site": {
                                "domain": "example.com",
                                "name": "example.com"
                            },
                            "enable_data_sharing_consent": true,
                            "enforce_data_sharing_consent": "at_login",
                            "enterprise_customer_users": [
                                1
                            ],
                            "branding_configuration": {
                                "enterprise_customer": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                                "logo": "https://open.edx.org/sites/all/themes/edx_open/logo.png"
                            },
                            "entitlements": [
                                {
                                    "entitlement_id": 69
                                }
                            ]
                        },
                        "user_id": 5,
                        "user": {
                            "username": "staff",
                            "first_name": "",
                            "last_name": "",
                            "email": "staff@example.com",
                            "is_staff": true,
                            "is_active": true,
                            "date_joined": "2016-09-01T19:18:26.026495Z"
                        },
                        "data_sharing_consent": [
                            {
                                "user": 1,
                                "state": "enabled",
                                "enabled": true
                            }
                        ]
                    }
                ],
                "next": null,
                "start": 0,
                "previous": null
            }
        }

    """
    resource = 'enterprise-learner'
    partner_code = site.siteconfiguration.partner.short_code
    cache_key = '{}_{}_{}.api.data'.format(site.domain, partner_code, resource)
    cache_key = hashlib.md5(cache_key).hexdigest()

    response = cache.get(cache_key)
    if not response:
        api = site.siteconfiguration.course_catalog_api_client
        endpoint = getattr(api, resource)
        querystring = {'username': learner.username}
        response = endpoint().get(**querystring)
        cache.set(cache_key, response, settings.ENTERPRISE_API_CACHE_TIMEOUT)

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
    for voucher in vouchers:
        is_valid_voucher, __ = voucher_is_valid(voucher, [product], request)
        if is_valid_voucher:
            voucher_course_ids = get_course_ids_from_voucher(request.site, voucher)
            if product.course_id in voucher_course_ids:
                return voucher


def get_course_ids_from_voucher(site, voucher):
    """
    Get site base list of course ids/keys from the provided voucher object.

    Arguments:
        site: (django.contrib.sites.Site) site instance
        voucher (Voucher): voucher class object

    Returns:
        list of course ids

    """
    voucher_offer = voucher.offers.first()
    offer_range = voucher_offer.condition.range
    if offer_range.course_catalog:
        course_catalog = get_course_catalogs(site=site, resource_id=offer_range.course_catalog)
        course_runs = get_catalog_course_runs(site, course_catalog.get('query'))
        voucher_course_ids = [course_run.get('key') for course_run in course_runs if course_run.get('key')]
    elif offer_range.catalog_query:
        course_runs = get_catalog_course_runs(site, offer_range.catalog_query)
        voucher_course_ids = [course_run.get('key') for course_run in course_runs if course_run.get('key')]
    else:
        stock_records = offer_range.catalog.stock_records.all()
        seats = Product.objects.filter(id__in=[sr.product.id for sr in stock_records])
        voucher_course_ids = [seat.course_id for seat in seats]

    return voucher_course_ids
