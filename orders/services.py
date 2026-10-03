from decimal import Decimal
from django.db import transaction
from .models import Order,OrderItem
from cart.models import Cart
def _number(): return f"RZ{Order.objects.count()+1:09d}"
@transaction.atomic
def create_order_from_cart(*,user,address):
    cart=Cart.objects.prefetch_related("items__variant__product").get(user=user)
    items=list(cart.items.all())
    if not items: raise ValueError("Cart is empty.")
    subtotal=Decimal("0")
    order=Order.objects.create(user=user,number=_number(),recipient_name=address.recipient_name,phone=address.phone,province=address.province,city=address.city,address=address.address,postal_code=address.postal_code)
    for item in items:
        variant=item.variant; line=variant.price*item.quantity; subtotal+=line
        OrderItem.objects.create(order=order,variant=variant,sku=variant.sku,product_name=variant.product.name,unit_price=variant.price,quantity=item.quantity,line_total=line)
    order.subtotal=order.grand_total=subtotal; order.save(update_fields=["subtotal","grand_total","updated_at"])
    cart.items.all().delete()
    return order
