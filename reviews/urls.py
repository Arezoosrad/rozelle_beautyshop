from django.urls import path
from .views import ProductReviewListCreateView
urlpatterns=[path("products/<int:product_id>/",ProductReviewListCreateView.as_view())]
