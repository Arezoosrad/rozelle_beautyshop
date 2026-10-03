from django.db import transaction
from django.core.exceptions import ValidationError
from .models import Stock
@transaction.atomic
def reserve_stock(*,variant,quantity):
    stock=Stock.objects.select_for_update().get(variant=variant)
    if stock.available_quantity < quantity: raise ValidationError("Insufficient stock.")
    stock.reserved_quantity += quantity; stock.save(update_fields=["reserved_quantity","updated_at"]); return stock
@transaction.atomic
def release_stock(*,variant,quantity):
    stock=Stock.objects.select_for_update().get(variant=variant)
    stock.reserved_quantity=max(stock.reserved_quantity-quantity,0); stock.save(update_fields=["reserved_quantity","updated_at"]); return stock
