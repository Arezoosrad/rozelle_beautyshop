from django.db import transaction
from .models import Payment
@transaction.atomic
def start_payment(*,order,gateway,amount):
    return Payment.objects.create(order=order,amount=amount,gateway=gateway)
@transaction.atomic
def mark_payment_success(*,payment,reference_id):
    payment.status=Payment.Status.SUCCESS; payment.reference_id=reference_id; payment.save(update_fields=["status","reference_id","updated_at"])
    payment.order.status=payment.order.Status.PAID; payment.order.save(update_fields=["status","updated_at"])
    return payment
