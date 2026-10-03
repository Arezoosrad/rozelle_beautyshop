from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import User, Address

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model=User
        fields=["id","username","email","first_name","last_name","role"]
        read_only_fields=["id","role"]

class RegisterSerializer(serializers.ModelSerializer):
    password=serializers.CharField(write_only=True,min_length=8)
    class Meta:
        model=User
        fields=["username","email","password","first_name","last_name"]
    def create(self,validated_data):
        return User.objects.create_user(**validated_data)

class LoginSerializer(serializers.Serializer):
    username=serializers.CharField()
    password=serializers.CharField(write_only=True)
    def validate(self,attrs):
        user=authenticate(username=attrs["username"],password=attrs["password"])
        if not user or not user.is_active: raise serializers.ValidationError("Invalid credentials.")
        attrs["user"]=user; return attrs

class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model=Address
        fields=["id","title","recipient_name","phone","province","city","address","postal_code","is_default"]
        read_only_fields=["id"]
