from django.db import transaction
from .models import Product,ProductVariant
@transaction.atomic
def create_product(*,brand,category,name,slug,description=""):
    return Product.objects.create(brand=brand,category=category,name=name,slug=slug,description=description)
@transaction.atomic
def add_variant(*,product,sku,title,price,attributes=None):
    return ProductVariant.objects.create(product=product,sku=sku,title=title,price=price,attributes=attributes or {})
