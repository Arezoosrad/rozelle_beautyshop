from django.urls import include,path
from .views import HealthView
urlpatterns=[path("health/",HealthView.as_view(),name="health")]
