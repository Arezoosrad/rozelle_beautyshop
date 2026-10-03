from rest_framework import generics,permissions
from .models import Review
from .serializers import ReviewSerializer
class ProductReviewListCreateView(generics.ListCreateAPIView):
    serializer_class=ReviewSerializer
    def get_queryset(self): return Review.objects.filter(product_id=self.kwargs["product_id"],is_approved=True)
    def perform_create(self,serializer):
        if not self.request.user.is_authenticated: raise permissions.NotAuthenticated()
        serializer.save(user=self.request.user,product_id=self.kwargs["product_id"])
