from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from catalog.models import ProductVariant
class Cart(TimeStampedModel):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="cart")
class CartItem(TimeStampedModel):
    cart=models.ForeignKey(Cart,on_delete=models.CASCADE,related_name="items")
    variant=models.ForeignKey(ProductVariant,on_delete=models.PROTECT,related_name="cart_items")
    quantity=models.PositiveIntegerField(default=1)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["cart","variant"],name="unique_cart_variant")]
