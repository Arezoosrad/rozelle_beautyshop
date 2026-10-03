from rest_framework import generics,permissions
from .models import Cart
from .serializers import CartSerializer
class CartView(generics.RetrieveAPIView):
    serializer_class=CartSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_object(self):
        cart,_=Cart.objects.get_or_create(user=self.request.user); return cart
