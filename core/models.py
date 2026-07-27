from django.db import models
from django.urls import reverse
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

class HookLog(models.Model):
    tabindex=4
    hook_name=models.CharField(max_length=255,verbose_name='Hook name',help_text='Hook identifier')
    callback_name=models.CharField(max_length=255,verbose_name='Callback name',help_text='Executed callback function')
    start_time=models.DateTimeField(auto_now_add=True,verbose_name='Start time',help_text='Execution start time')
    end_time=models.DateTimeField(null=True,blank=True,verbose_name='End time',help_text='Execution end time')
    execution_time_ms=models.FloatField(null=True,blank=True,verbose_name='Execution time (ms)',help_text='Execution duration in milliseconds')
    status=models.CharField(max_length=50,default='success',verbose_name='Status',help_text='Execution status')
    error_message=models.TextField(blank=True,null=True,verbose_name='Error message',help_text='Error message if execution failed')
    trashed=models.BooleanField(default=False,verbose_name='Trashed',help_text='Marked as deleted')
    
    class Meta:
        verbose_name='Hook log'
        verbose_name_plural='Hook logs'
        
    def __str__(self):
        return f'{self.hook_name} - {self.callback_name} ({self.status})'
    
class CronJob(models.Model):
    tabindex=4
    name=models.CharField(max_length=255,unique=True,verbose_name='Name',help_text='Cron job name')
    func_path=models.CharField(max_length=500,verbose_name='Function path',help_text='Callable function path')
    cron_expression=models.CharField(max_length=100,verbose_name='Cron expression',help_text='Cron expression or preset')
    active=models.BooleanField(default=True,verbose_name='Active',help_text='Is cron job active')
    last_run=models.DateField(null=True,blank=True,verbose_name='Last run date',help_text='Last execution date')
    last_run_time=models.TimeField(null=True,blank=True,verbose_name='Last run time',help_text='Last execution time')
    next_run=models.DateField(null=True,blank=True,verbose_name='Next run date',help_text='Next execution date')
    next_run_time=models.TimeField(null=True,blank=True,verbose_name='Next run time',help_text='Next execution time')
    trashed=models.BooleanField(default=False,verbose_name='Trashed',help_text='Marked as deleted')
    
    class Meta:
        verbose_name='Scheduled job'
        verbose_name_plural='Scheduled jobs'
        
    def __str__(self):
        return self.name
    