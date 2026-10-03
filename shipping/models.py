from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
class Shipment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING="pending","Pending"; READY="ready","Ready"; SHIPPED="shipped","Shipped"; DELIVERED="delivered","Delivered"
    order=models.OneToOneField(Order,on_delete=models.PROTECT,related_name="shipment")
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.PENDING)
    carrier=models.CharField(max_length=80,blank=True); tracking_code=models.CharField(max_length=120,blank=True)
