from django.urls import include,path
from .views import HealthView
urlpatterns=[
 path("health/",HealthView.as_view(),name="health"),
 path("accounts/",include("accounts.urls")),
 path("catalog/",include("catalog.urls")),
 path("cart/",include("cart.urls")),
 path("orders/",include("orders.urls")),
 path("payments/",include("payments.urls")),
 path("shipping/",include("shipping.urls")),
 path("promotions/",include("promotions.urls")),
 path("reviews/",include("reviews.urls")),
]