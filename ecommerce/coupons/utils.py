""" Coupon related utility functions. """
import hashlib

from django.conf import settings
from django.core.cache import cache
from oscar.core.loading import get_model

from ecommerce.courses.utils import traverse_pagination


Product = get_model('catalogue', 'Product')


def get_range_catalog_query_results(limit, query, site, offset=None):
    """
    Get catalog query results

    Arguments:
        limit (int): Number of results per page
        query (str): ElasticSearch Query
        site (Site): Site object containing Site Configuration data
        offset (int): Page offset

    Returns:
        dict: Query seach results received from Course Catalog API
    """
    partner_code = site.siteconfiguration.partner.short_code
    cache_key = 'course_runs_{}_{}_{}_{}'.format(query, limit, offset, partner_code)
    cache_key = hashlib.md5(cache_key).hexdigest()
    response = cache.get(cache_key)
    if not response:
        response = site.siteconfiguration.course_catalog_api_client.course_runs.get(
            limit=limit,
            offset=offset,
            q=query,
            partner=partner_code
        )
        cache.set(cache_key, response, settings.COURSES_API_CACHE_TIMEOUT)
    return response


def get_all_range_catalog_query_results(site, query):
    """
    Get all catalogs on the basis of provided query from the Course Catalog
    API by recursively retrieving API endpoint.

    Arguments:
        query (str): ElasticSearch Query
        site (Site): Site object containing Site Configuration data

    Returns:
        dict: Query search results received from Course Catalog API

    """
    resource = 'course_runs'
    partner_code = site.siteconfiguration.partner.short_code
    cache_key = '{}_{}_{}_{}.course_runs.api.data'.format(site.domain, resource, partner_code, query)
    cache_key = hashlib.md5(cache_key).hexdigest()

    response = cache.get(cache_key)
    if not response:
        api = site.siteconfiguration.course_catalog_api_client
        endpoint = getattr(api, resource)
        response = endpoint().get(
            q=query,
            partner=partner_code
        )

        response = traverse_pagination(response, endpoint)
        cache.set(cache_key, response, settings.COURSES_API_CACHE_TIMEOUT)

    return response


def prepare_course_seat_types(course_seat_types):
    """
    Convert list of course seat types into comma-separated string.

    Arguments:
        course_seat_types (list): List of course seat types

    Returns:
        str: Comma-separated list of course seat types if course_seat_types is not empty
    """
    return ','.join(seat_type.lower() for seat_type in course_seat_types)
