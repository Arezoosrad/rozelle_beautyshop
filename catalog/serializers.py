from rest_framework import serializers
from .models import Product,ProductVariant
class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model=ProductVariant; fields=["id","sku","title","price","compare_at_price","attributes"]
class ProductSerializer(serializers.ModelSerializer):
    variants=ProductVariantSerializer(many=True,read_only=True)
    class Meta:
        model=Product; fields=["id","name","slug","description","brand","category","variants"]
