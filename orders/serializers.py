from rest_framework import serializers
from .models import Order,OrderItem
class OrderItemSerializer(serializers.ModelSerializer):
    class Meta: model=OrderItem; fields=["sku","product_name","unit_price","quantity","line_total"]
class OrderSerializer(serializers.ModelSerializer):
    items=OrderItemSerializer(many=True,read_only=True)
    class Meta: model=Order; fields=["number","status","subtotal","discount_total","shipping_total","grand_total","created_at","items"]
