from rest_framework import generics,permissions
from .models import Order
from .serializers import OrderSerializer
class OrderListView(generics.ListAPIView):
    serializer_class=OrderSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_queryset(self): return Order.objects.filter(user=self.request.user).prefetch_related("items").order_by("-created_at")
