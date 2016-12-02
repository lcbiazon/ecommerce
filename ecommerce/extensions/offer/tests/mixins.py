# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from oscar.test.factories import create_order
from oscar.test.newfactories import BasketFactory

from ecommerce.courses.models import Course
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.extensions.fulfillment.status import ORDER


class OfferTestMixin(CourseCatalogTestMixin):
    def setUp(self):
        super(OfferTestMixin, self).setUp()

        self.course, __ = Course.objects.get_or_create(id='edX/DemoX/Demo_Course', name='edX Demó Course')
        self.honor_product = self.course.create_or_update_seat('honor', False, 0, self.partner)
        self.verified_product = self.course.create_or_update_seat('verified', True, 10, self.partner)

    def create_order(self, user=None, multiple_lines=False, free=False, status=ORDER.COMPLETE):
        user = user or self.user
        basket = BasketFactory(owner=user)

        if multiple_lines:
            basket.add_product(self.verified_product)
            basket.add_product(self.honor_product)
        elif free:
            basket.add_product(self.honor_product)
        else:
            basket.add_product(self.verified_product)

        order = create_order(basket=basket, user=user)
        order.status = status
        order.save()
        return order
