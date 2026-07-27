import traceback
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.shortcuts import render
from django.urls import path, include
from django.conf.urls.static import static
from django.conf.urls import handler400, handler403, handler404, handler500
from django.views.decorators.csrf import csrf_exempt as _csrf_exempt  # noqa: E402

admin.autodiscover()

urlpatterns = [
	path("__reload__/", include("django_browser_reload.urls")),
	path('ckeditor/', include('ckeditor_uploader.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

for config in apps.get_app_configs():
	if hasattr(config, 'get_url_root'):
		prefix, module, namespace = config.get_url_root()
		urlpatterns.append(path(prefix, include((module, namespace))))

urlpatterns += [path('admin_core/', admin.site.urls)]

for _pattern in urlpatterns:
    _pattern.callback = _csrf_exempt(_pattern.callback)

import traceback
from django.shortcuts import render
from django.conf import settings

def error_response_handler(request, status_code, exception=None):
    """
    تابع مرکزی برای مدیریت تمامی خطاهای HTTP و رندر کردن یک قالب واحد.
    """
    
    # نقشه پیام‌های فارسی برای هر کد وضعیت
    error_messages = {
        400: {
            "title": "درخواست نامعتبر (Bad Request)",
            "message": "سرور نمی‌تواند درخواست شما را به دلیل خطای نحوی پردازش کند."
        },
        403: {
            "title": "دسترسی غیرمجاز (Forbidden)",
            "message": "شما اجازه دسترسی به این منبع را ندارید."
        },
        404: {
            "title": "صفحه یافت نشد (Not Found)",
            "message": "متأسفانه صفحه‌ای که به دنبال آن هستید وجود ندارد یا حذف شده است."
        },
        500: {
            "title": "خطای داخلی سرور (Internal Server Error)",
            "message": "مشکلی در سمت سرور رخ داده است. در حال تلاش برای رفع آن هستیم."
        },
    }

    # دریافت پیام متناسب با کد، یا استفاده از پیام پیش‌فرض
    error_info = error_messages.get(status_code, {
        "title": f"خطای {status_code}",
        "message": "یک خطای غیرمنتظره رخ داده است."
    })

    context = {
        "status_code": status_code,
        "title": error_info["title"],
        "message": error_info["message"],
        "exception_detail": str(exception) if exception else None,
        "debug": settings.TRACE,
    }

    # افزودن Traceback در حالت Debug برای عیب‌یابی سریع‌تر
    if settings.TRACE:
        context["traceback"] = traceback.format_exc()

    return render(request, "core/error_page.html", context, status=status_code)

# نگاشت هندلرهای جنگو به تابع مرکزی
def handler400(request, exception=None):
    return error_response_handler(request, 400, exception)

def handler403(request, exception=None):
    return error_response_handler(request, 403, exception)

def handler404(request, exception=None):
    return error_response_handler(request, 404, exception)

def handler500(request):
    return error_response_handler(request, 500)

handler400 = handler400
handler403 = handler403
handler404 = handler404
handler500 = handler500