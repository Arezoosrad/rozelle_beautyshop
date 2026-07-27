# core/setting_middleware.py
from django.template.response import TemplateResponse
from django.http import HttpResponsePermanentRedirect
import re

class RemoveDoubleSlashesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.get_full_path()
        # بررسی اینکه آیا دبل‌اسلش در مسیر وجود دارد (به جز پروتکل)
        if '//' in request.path:
            # پاک کردن دبل‌اسلش‌ها
            clean_path = re.sub(r'/+', '/', path)
            # اگر پارامتر کوئری وجود دارد، اصلاح آدرس بدون خراب کردن پروتکل
            if request.is_secure():
                new_url = f"https://{request.get_host()}{clean_path}"
            else:
                new_url = f"http://{request.get_host()}{clean_path}"
            
            return HttpResponsePermanentRedirect(new_url)
            
        return self.get_response(request)
		
class SiteSettingsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if isinstance(response, TemplateResponse):
            from .models import SiteSetting
            
            try:
                settings = SiteSetting.objects.first()
            except SiteSetting.DoesNotExist:
                settings = None
            response.context_data['site_settings'] = settings
            # print(response.context_data['site_settings'])
        return response
