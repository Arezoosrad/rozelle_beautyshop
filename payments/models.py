from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
class Payment(TimeStampedModel):
    class Status(models.TextChoices):
        INITIATED="initiated","Initiated"; SUCCESS="success","Success"; FAILED="failed","Failed"
    order=models.ForeignKey(Order,on_delete=models.PROTECT,related_name="payments")
    amount=models.DecimalField(max_digits=12,decimal_places=0)
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.INITIATED,db_index=True)
    gateway=models.CharField(max_length=40); authority=models.CharField(max_length=120,blank=True); reference_id=models.CharField(max_length=120,blank=True)
