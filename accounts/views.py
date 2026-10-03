from django.contrib.auth import login,logout
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import generics,permissions,status
from rest_framework.decorators import api_view,permission_classes
from rest_framework.response import Response
from .models import Address
from .serializers import UserSerializer,RegisterSerializer,LoginSerializer,AddressSerializer

@ensure_csrf_cookie
@api_view(["GET"])
def csrf_view(request): return Response({"detail":"CSRF cookie set."})

@api_view(["POST"])
def register_view(request):
    serializer=RegisterSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    user=serializer.save(); login(request,user); return Response(UserSerializer(user).data,status=status.HTTP_201_CREATED)

@api_view(["POST"])
def login_view(request):
    serializer=LoginSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    login(request,serializer.validated_data["user"]); return Response(UserSerializer(serializer.validated_data["user"]).data)

@api_view(["POST"])
def logout_view(request):
    logout(request); return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def me_view(request): return Response(UserSerializer(request.user).data)

class AddressListCreateView(generics.ListCreateAPIView):
    serializer_class=AddressSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_queryset(self): return Address.objects.filter(user=self.request.user).order_by("-is_default","-created_at")
    def perform_create(self,serializer): serializer.save(user=self.request.user)

class AddressDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class=AddressSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_queryset(self): return Address.objects.filter(user=self.request.user)
