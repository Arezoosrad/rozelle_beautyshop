from django.db import models
from core.models import TimeStampedModel
from catalog.models import ProductVariant
class Stock(TimeStampedModel):
    variant=models.OneToOneField(ProductVariant,on_delete=models.CASCADE,related_name="stock")
    quantity=models.PositiveIntegerField(default=0); reserved_quantity=models.PositiveIntegerField(default=0)
    low_stock_threshold=models.PositiveIntegerField(default=0)
    @property
    def available_quantity(self): return max(self.quantity-self.reserved_quantity,0)
