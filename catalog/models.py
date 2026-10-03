from django.db import models
from core.models import SoftDeleteModel
class Brand(SoftDeleteModel):
    name=models.CharField(max_length=120,unique=True); slug=models.SlugField(max_length=140,unique=True)
class Category(SoftDeleteModel):
    name=models.CharField(max_length=120); slug=models.SlugField(max_length=140,unique=True)
    parent=models.ForeignKey("self",null=True,blank=True,on_delete=models.PROTECT,related_name="children")
class Product(SoftDeleteModel):
    brand=models.ForeignKey(Brand,on_delete=models.PROTECT,related_name="products")
    category=models.ForeignKey(Category,on_delete=models.PROTECT,related_name="products")
    name=models.CharField(max_length=200); slug=models.SlugField(max_length=220,unique=True)
    description=models.TextField(blank=True); is_active=models.BooleanField(default=True,db_index=True)
class ProductVariant(SoftDeleteModel):
    product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name="variants")
    sku=models.CharField(max_length=80,unique=True)
    title=models.CharField(max_length=160,blank=True)
    price=models.DecimalField(max_digits=12,decimal_places=0)
    compare_at_price=models.DecimalField(max_digits=12,decimal_places=0,null=True,blank=True)
    attributes=models.JSONField(default=dict,blank=True)
    is_active=models.BooleanField(default=True,db_index=True)
class ProductImage(SoftDeleteModel):
    product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name="images")
    image=models.ImageField(upload_to="products/")
    alt=models.CharField(max_length=160,blank=True); sort_order=models.PositiveIntegerField(default=0)
