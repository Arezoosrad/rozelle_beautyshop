from django.db import transaction
from .models import Cart,CartItem
@transaction.atomic
def get_or_create_cart(*,user):
    return Cart.objects.get_or_create(user=user)
@transaction.atomic
def add_item(*,user,variant,quantity=1):
    cart,_=get_or_create_cart(user=user)
    item,created=CartItem.objects.select_for_update().get_or_create(cart=cart,variant=variant,defaults={"quantity":quantity})
    if not created: item.quantity += quantity; item.save(update_fields=["quantity","updated_at"])
    return item
