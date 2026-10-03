from django.conf import settings
from django.db import models
class TimeStampedModel(models.Model):
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True
class SoftDeleteModel(TimeStampedModel):
    is_deleted=models.BooleanField(default=False,db_index=True)
    class Meta: abstract=True
class AuditLog(TimeStampedModel):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="audit_logs")
    action=models.CharField(max_length=64); model_name=models.CharField(max_length=128); object_id=models.CharField(max_length=64,blank=True); metadata=models.JSONField(default=dict,blank=True)
