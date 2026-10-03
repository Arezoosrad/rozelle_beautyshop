from celery import shared_task
@shared_task
def expire_pending_payments():
    from django.utils import timezone
    from datetime import timedelta
    from .models import Payment
    cutoff=timezone.now()-timedelta(hours=2)
    Payment.objects.filter(status=Payment.Status.INITIATED,created_at__lt=cutoff).update(status=Payment.Status.FAILED)
