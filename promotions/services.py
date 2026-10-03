from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Coupon
def validate_coupon(*,code,order_total):
    coupon=Coupon.objects.filter(code=code.upper(),is_active=True,starts_at__lte=timezone.now(),ends_at__gte=timezone.now()).first()
    if not coupon: raise ValidationError("Invalid coupon.")
    if order_total < coupon.minimum_order_amount: raise ValidationError("Order total is below coupon minimum.")
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses: raise ValidationError("Coupon usage limit reached.")
    return coupon
