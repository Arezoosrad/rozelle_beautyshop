from rest_framework import serializers
from .models import Cart,CartItem
class CartItemSerializer(serializers.ModelSerializer):
    class Meta: model=CartItem; fields=["id","variant","quantity"]
class CartSerializer(serializers.ModelSerializer):
    items=CartItemSerializer(many=True,read_only=True)
    class Meta: model=Cart; fields=["id","items"]
