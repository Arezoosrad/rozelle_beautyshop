from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from catalog.models import ProductVariant
class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING="pending","Pending"; PAID="paid","Paid"; PROCESSING="processing","Processing"; SHIPPED="shipped","Shipped"; DELIVERED="delivered","Delivered"; CANCELED="canceled","Canceled"
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="orders")
    number=models.CharField(max_length=32,unique=True)
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.PENDING,db_index=True)
    subtotal=models.DecimalField(max_digits=12,decimal_places=0,default=0)
    discount_total=models.DecimalField(max_digits=12,decimal_places=0,default=0)
    shipping_total=models.DecimalField(max_digits=12,decimal_places=0,default=0)
    grand_total=models.DecimalField(max_digits=12,decimal_places=0,default=0)
    recipient_name=models.CharField(max_length=120); phone=models.CharField(max_length=20)
    province=models.CharField(max_length=80); city=models.CharField(max_length=80); address=models.TextField(); postal_code=models.CharField(max_length=20)
class OrderItem(TimeStampedModel):
    order=models.ForeignKey(Order,on_delete=models.CASCADE,related_name="items")
    variant=models.ForeignKey(ProductVariant,on_delete=models.PROTECT,related_name="order_items")
    sku=models.CharField(max_length=80); product_name=models.CharField(max_length=200)
    unit_price=models.DecimalField(max_digits=12,decimal_places=0); quantity=models.PositiveIntegerField()
    line_total=models.DecimalField(max_digits=12,decimal_places=0)
