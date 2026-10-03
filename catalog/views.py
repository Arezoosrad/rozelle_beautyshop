from rest_framework import generics
from .serializers import ProductSerializer
from .selectors import list_active_products
class ProductListView(generics.ListAPIView):
    serializer_class=ProductSerializer
    search_fields=["name","slug","description","brand__name","category__name"]
    ordering_fields=["created_at","name"]
    def get_queryset(self): return list_active_products()
class ProductDetailView(generics.RetrieveAPIView):
    serializer_class=ProductSerializer; lookup_field="slug"
    def get_queryset(self): return list_active_products()
