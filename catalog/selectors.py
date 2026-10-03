from .models import Product
def list_active_products():
    return Product.objects.filter(is_deleted=False,is_active=True).select_related("brand","category").prefetch_related("variants","images")
