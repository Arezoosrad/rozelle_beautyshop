from django.contrib.auth.models import AbstractUser
from django.db import models
from core.models import TimeStampedModel
class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER="customer","Customer"; STAFF="staff","Staff"; ADMIN="admin","Admin"
    role=models.CharField(max_length=20,choices=Role.choices,default=Role.CUSTOMER)
class Address(TimeStampedModel):
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="addresses")
    title=models.CharField(max_length=80)
    recipient_name=models.CharField(max_length=120)
    phone=models.CharField(max_length=20)
    province=models.CharField(max_length=80); city=models.CharField(max_length=80)
    address=models.TextField(); postal_code=models.CharField(max_length=20)
    is_default=models.BooleanField(default=False)
