from django.urls import path
from .views import csrf_view,register_view,login_view,logout_view,me_view,AddressListCreateView,AddressDetailView
urlpatterns=[path("csrf/",csrf_view),path("register/",register_view),path("login/",login_view),path("logout/",logout_view),path("me/",me_view),path("addresses/",AddressListCreateView.as_view()),path("addresses/<int:pk>/",AddressDetailView.as_view())]
