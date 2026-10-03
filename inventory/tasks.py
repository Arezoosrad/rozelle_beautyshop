from celery import shared_task
@shared_task
def low_stock_variant_ids():
    from .models import Stock
    return list(Stock.objects.filter(quantity__lte=models.F("low_stock_threshold")).values_list("variant_id",flat=True))
