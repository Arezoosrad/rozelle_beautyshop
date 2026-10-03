from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from catalog.models import Product
class Review(TimeStampedModel):
    product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name="reviews")
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="reviews")
    rating=models.PositiveSmallIntegerField(); title=models.CharField(max_length=160,blank=True); body=models.TextField(blank=True)
    is_approved=models.BooleanField(default=False,db_index=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["product","user"],name="unique_product_review_user")]
