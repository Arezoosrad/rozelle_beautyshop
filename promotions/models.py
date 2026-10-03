from django.db import models
from core.models import TimeStampedModel
class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENT="percent","Percent"; FIXED="fixed","Fixed"
    code=models.CharField(max_length=40,unique=True)
    discount_type=models.CharField(max_length=20,choices=DiscountType.choices)
    value=models.DecimalField(max_digits=12,decimal_places=0)
    minimum_order_amount=models.DecimalField(max_digits=12,decimal_places=0,default=0)
    max_uses=models.PositiveIntegerField(null=True,blank=True); used_count=models.PositiveIntegerField(default=0)
    starts_at=models.DateTimeField(); ends_at=models.DateTimeField(); is_active=models.BooleanField(default=True)
