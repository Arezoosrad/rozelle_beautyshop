from celery import shared_task
@shared_task
def process_order(order_id):
    from .models import Order
    order=Order.objects.get(pk=order_id)
    if order.status==Order.Status.PAID:
        order.status=Order.Status.PROCESSING; order.save(update_fields=["status","updated_at"])
