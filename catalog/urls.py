from django.urls import path
from .views import ProductListView,ProductDetailView
urlpatterns=[path("products/",ProductListView.as_view()),path("products/<slug:slug>/",ProductDetailView.as_view())]
