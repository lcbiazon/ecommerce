from django.conf import settings
from django.test import override_settings
import mock
from mock_django import mock_signal_receiver
from oscar.core.loading import get_model, get_class

from ecommerce.extensions.offer.tests.mixins import OfferTestMixin

from ecommerce.extensions.payment.tests.processors import DummyProcessor
from ecommerce.extensions.refund.status import REFUND, REFUND_LINE
from ecommerce.extensions.refund.tests.factories import RefundFactory

post_refund = get_class('refund.signals', 'post_refund')
Refund = get_model('refund', 'Refund')
Source = get_model('payment', 'Source')
SourceType = get_model('payment', 'SourceType')


class RefundTestMixin(OfferTestMixin):
    def assert_refund_matches_order(self, refund, order):
        """ Verify the refund corresponds to the given order. """
        self.assertEqual(refund.order, order)
        self.assertEqual(refund.user, order.user)
        self.assertEqual(refund.status, settings.OSCAR_INITIAL_REFUND_STATUS)
        self.assertEqual(refund.total_credit_excl_tax, order.total_excl_tax)
        self.assertEqual(refund.lines.count(), order.lines.count())

        refund_lines = refund.lines.all()
        order_lines = order.lines.all().order_by('refund_lines')
        for refund_line, order_line in zip(refund_lines, order_lines):
            self.assertEqual(refund_line.status, settings.OSCAR_INITIAL_REFUND_LINE_STATUS)
            self.assertEqual(refund_line.order_line, order_line)
            self.assertEqual(refund_line.line_credit_excl_tax, order_line.line_price_excl_tax)
            self.assertEqual(refund_line.quantity, order_line.quantity)

    def create_refund(self, processor_name=DummyProcessor.NAME):
        refund = RefundFactory()
        order = refund.order
        source_type, __ = SourceType.objects.get_or_create(name=processor_name)
        Source.objects.create(source_type=source_type, order=order, currency=refund.currency,
                              amount_allocated=order.total_incl_tax, amount_debited=order.total_incl_tax)

        return refund

    @override_settings(PAYMENT_PROCESSORS=['ecommerce.extensions.payment.tests.processors.DummyProcessor'])
    def approve(self, refund):
        def _revoke_lines(r):
            for line in r.lines.all():
                line.set_status(REFUND_LINE.COMPLETE)

            r.set_status(REFUND.COMPLETE)

        with mock.patch.object(Refund, '_revoke_lines', side_effect=_revoke_lines, autospec=True):
            with mock_signal_receiver(post_refund) as receiver:
                self.assertEqual(receiver.call_count, 0)
                self.assertTrue(refund.approve())
                self.assertEqual(receiver.call_count, 1)
