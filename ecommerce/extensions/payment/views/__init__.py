from django.views.generic import TemplateView


class PaymentFailedView(TemplateView):
    template_name = 'checkout/cybersource_error.html'
    # TODO Finish me!


class PaymentCancelledView(TemplateView):
    # TODO Finish me!
    pass
