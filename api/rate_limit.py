"""
rate_limit.py - محدودیت نرخ (Rate Limiting) برای API

این ماژول کنترل نرخ درخواست‌ها را بر اساس کاربر، IP و مدل مدیریت می‌کند.
از Redis برای پیاده‌سازی استفاده می‌کند.

نحوه استفاده:

    from .rate_limit import check_rate_limit, RateLimitError

    # محدودیت ۱۰ درخواست در دقیقه برای هر کاربر
    check_rate_limit(
        user=user,
        endpoint='/api/customer/',
        max_requests=10,
        window_seconds=60
    )

    # محدودیت ۵ درخواست در دقیقه برای متدهای خاص
    check_rate_limit(
        user=user,
        endpoint='/api/customer/call/send_otp',
        max_requests=5,
        window_seconds=60,
        key_prefix='custom_otp_'
    )
"""

import time
import hashlib
import hmac
from typing import Optional
from functools import wraps

# تلاش برای بارگذاری Redis، اگر موجود نبود از cache استفاده می‌کنیم
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import User, AnonymousUser
from django.utils.functional import SimpleLazyObject


# ============================================================================
# تنظیمات پیش‌فرض
# ============================================================================

DEFAULT_RATE_LIMIT_MAX_REQUESTS = 100  # تعداد درخواست در هر بازه زمانی
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60  # بازه زمانی به ثانیه
DEFAULT_RATE_LIMIT_KEY_PREFIX = 'api_rate_limit'


# ============================================================================
# مدیریت اتصال به Redis
# ============================================================================

_redis_client = None


def get_redis_client():
    """
    دریافت یا ایجاد اتصال Redis
    
    Returns:
        اتصال Redis یا None اگر Redis نصب نباشد
    """
    global _redis_client
    if _redis_client is None and REDIS_AVAILABLE:
        redis_url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
        try:
            _redis_client = redis.from_url(
                redis_url,
                socket_timeout=5,
                socket_connect_timeout=5,
                decode_responses=True
            )
            # تست اتصال
            _redis_client.ping()
        except (Exception, redis.exceptions.ConnectionError):
            _redis_client = None
            print('⚠️ اتصال به Redis برقرار نشد. از cache پیش‌فرض استفاده می‌شود.')
    
    return _redis_client


# ============================================================================
# توابع کمکی برای ساخت کلید
# ============================================================================

def _generate_cache_key(key_prefix: str, identifier: str) -> str:
    """
    ساخت کلید یکتا برای cache
    
    Args:
        key_prefix: پیشوند کلید (مثل 'api_rate_limit')
        identifier: شناسه منحصر به فرد (مثل user_id یا ip_address)
    
    Returns:
        کلید cache
    """
    # اگر شناسه طولانی باشد، هش می‌کنیم
    if len(identifier) > 128:
        identifier = hashlib.sha256(identifier.encode()).hexdigest()[:32]
    
    return f'{key_prefix}:{identifier}'


def _generate_identifier(user: User, ip_address: str, endpoint: str) -> str:
    """
    تولید شناسه منحصر به فرد بر اساس کاربر، IP و endpoint
    
    Args:
        user: کاربر
        ip_address: آدرس IP
        endpoint: مسیر API
    
    Returns:
        شناسه منحصر به فرد
    """
    if isinstance(user, AnonymousUser):
        # برای کاربران مهمان بر اساس IP
        return f'anonymous:{ip_address}'
    
    # برای کاربران ثبت‌نام‌شده بر اساس user_id
    return f'user:{user.id}'


# ============================================================================
# کلاس RateLimitError
# ============================================================================

class RateLimitError(Exception):
    """
    استثنا برای وقتی که کاربر از سقف نرخ درخواست عبور می‌کند
    """
    
    def __init__(
        self,
        message: str = 'سقف نرخ درخواست‌ها عبور شده است. لطفاً بعداً دوباره تلاش کنید.',
        retry_after: int = 0
    ):
        self.message = message
        self.retry_after = retry_after
        super().__init__(self.message)


# ============================================================================
# توابع اصلی Rate Limiting
# ============================================================================

def check_rate_limit(
    user: User,
    endpoint: str,
    max_requests: Optional[int] = None,
    window_seconds: Optional[int] = None,
    key_prefix: Optional[str] = None,
    ip_address: Optional[str] = None
) -> dict:
    """
    بررسی اینکه آیا کاربر از سقف نرخ درخواست عبور کرده است
    
    این تابع تعداد درخواست‌های کاربر در بازه زمانی مشخص را 
    شمارش می‌کند و اگر از حد مجاز بیشتر باشد، RateLimitError 
    پرتاب می‌کند.
    
    Args:
        user: کاربر فعلی
        endpoint: مسیر API (مثل '/api/customer/')
        max_requests: حداکثر تعداد درخواست در بازه زمانی
        window_seconds: بازه زمانی به ثانیه
        key_prefix: پیشوند کلید cache (اختیاری)
        ip_address: آدرس IP کاربر (برای کاربران مهمان)
    
    Returns:
        Dictionary حاوی اطلاعات نرخ:
        {
            'current_requests': 5,
            'max_requests': 100,
            'window_seconds': 60,
            'remaining_requests': 95,
            'reset_at': 1234567890,
            'retry_after': 0
        }
    
    Raises:
        RateLimitError: اگر از سقف نرخ عبور کرده باشد
    """
    # تنظیم مقادیر پیش‌فرض
    max_requests = max_requests or DEFAULT_RATE_LIMIT_MAX_REQUESTS
    window_seconds = window_seconds or DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    key_prefix = key_prefix or DEFAULT_RATE_LIMIT_KEY_PREFIX
    
    # دریافت IP از header یا request
    if not ip_address:
        ip_address = _get_client_ip()
    
    # تولید شناسه یکتا
    identifier = _generate_identifier(user, ip_address, endpoint)
    
    # ساخت کلید cache
    cache_key = _generate_cache_key(key_prefix, identifier)
    
    # تلاش برای استفاده از Redis
    client = get_redis_client()
    
    if client:
        return _check_rate_limit_redis(client, cache_key, max_requests, window_seconds)
    else:
        return _check_rate_limit_cache(cache_key, max_requests, window_seconds)


def _check_rate_limit_redis(client, cache_key: str, max_requests: int, window_seconds: int) -> dict:
    """
    بررسی نرخ با استفاده از Redis
    
    از Lua Script برای اطمینان از atomic بودن عملیات استفاده می‌کند.
    
    Args:
        client: اتصال Redis
        cache_key: کلید cache
        max_requests: حداکثر تعداد درخواست
        window_seconds: بازه زمانی
    
    Returns:
        Dictionary حاوی اطلاعات نرخ
    """
    # اسکریپت Lua برای atomic increment و expiry
    lua_script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or "0")
    if current == 0 then
        redis.call('INCR', KEYS[1])
        redis.call('EXPIRE', KEYS[1], ARGV[1])
        return 1
    end
    current = redis.call('INCR', KEYS[1])
    if current == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return current
    """
    
    # اجرای اسکریپت
    current_requests = int(client.eval(lua_script, 1, cache_key, window_seconds))
    reset_time = int(time.time()) + window_seconds
    
    # بررسی سقف
    if current_requests > max_requests:
        remaining = 0
        retry_after = reset_time - int(time.time())
        raise RateLimitError(
            message=f'سقف {max_requests} درخواست در {window_seconds} ثانیه عبور شده است.',
            retry_after=max(retry_after, 1)
        )
    
    remaining = max_requests - current_requests
    
    return {
        'current_requests': current_requests,
        'max_requests': max_requests,
        'window_seconds': window_seconds,
        'remaining_requests': remaining,
        'reset_at': reset_time,
        'retry_after': 0
    }


def _check_rate_limit_cache(cache_key: str, max_requests: int, window_seconds: int) -> dict:
    """
    بررسی نرخ با استفاده از cache پیش‌فرض Django
    
    Args:
        cache_key: کلید cache
        max_requests: حداکثر تعداد درخواست
        window_seconds: بازه زمانی
    
    Returns:
        Dictionary حاوی اطلاعات نرخ
    """
    # افزایش شمارنده
    current = cache.incr(cache_key)
    
    # اگر اولین درخواست است، expiry را تنظیم می‌کنیم
    if current == 1:
        cache.expire(cache_key, window_seconds)
    
    reset_time = int(time.time()) + window_seconds
    
    # بررسی سقف
    if current > max_requests:
        remaining = 0
        retry_after = reset_time - int(time.time())
        raise RateLimitError(
            message=f'سقف {max_requests} درخواست در {window_seconds} ثانیه عبور شده است.',
            retry_after=max(retry_after, 1)
        )
    
    remaining = max_requests - current
    
    return {
        'current_requests': current,
        'max_requests': max_requests,
        'window_seconds': window_seconds,
        'remaining_requests': remaining,
        'reset_at': reset_time,
        'retry_after': 0
    }


def get_remaining_requests(
    user: User,
    endpoint: str,
    max_requests: int = DEFAULT_RATE_LIMIT_MAX_REQUESTS,
    window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    ip_address: Optional[str] = None
) -> dict:
    """
    دریافت اطلاعات نرخ بدون افزایش شمارنده (فقط خواندن)
    
    این تابع برای بررسی وضعیت فعلی بدون تأثیر بر شمارنده استفاده می‌شود.
    
    Args:
        user: کاربر
        endpoint: مسیر API
        max_requests: حداکثر تعداد درخواست
        window_seconds: بازه زمانی
        ip_address: آدرس IP
    
    Returns:
        Dictionary حاوی اطلاعات نرخ فعلی
    """
    if not ip_address:
        ip_address = _get_client_ip()
    
    identifier = _generate_identifier(user, ip_address, endpoint)
    cache_key = _generate_cache_key(DEFAULT_RATE_LIMIT_KEY_PREFIX, identifier)
    
    client = get_redis_client()
    if client:
        current = int(client.get(cache_key) or 0)
        ttl = client.ttl(cache_key)
    else:
        current = cache.get(cache_key) or 0
        ttl = cache.ttl(cache_key)
    
    remaining = max(max_requests - current, 0)
    reset_at = int(time.time()) + (ttl if ttl > 0 else window_seconds)
    
    return {
        'current_requests': current,
        'max_requests': max_requests,
        'remaining_requests': remaining,
        'reset_at': reset_at,
        'window_seconds': window_seconds,
    }


# ============================================================================
# دکوراتور Rate Limiting
# ============================================================================

def rate_limited(
    max_requests: Optional[int] = None,
    window_seconds: Optional[int] = None,
    key_prefix: Optional[str] = None,
    endpoint: Optional[str] = None
):
    """
    دکوراتور برای اعمال Rate Limiting روی view‌ها
    
    مثال استفاده:
    
        @rate_limited(max_requests=10, window_seconds=60)
        def my_view(request):
            # این view محدود به ۱۰ درخواست در دقیقه است
            pass
    
    Args:
        max_requests: حداکثر تعداد درخواست
        window_seconds: بازه زمانی
        key_prefix: پیشوند کلید cache
        endpoint: مسیر API (اختیاری)
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # بررسی نرخ
            try:
                check_rate_limit(
                    user=request.user,
                    endpoint=endpoint or request.path,
                    max_requests=max_requests,
                    window_seconds=window_seconds,
                    key_prefix=key_prefix,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
            except RateLimitError as e:
                # ساخت پاسخ خطای 429
                from django.http import HttpResponseForbidden
                response = HttpResponseForbidden(e.message)
                response['Retry-After'] = str(e.retry_after)
                response['Content-Type'] = 'application/json; charset=utf-8'
                return response
            
            # فراخوانی view اصلی
            return view_func(request, *args, **kwargs)
        
        return wrapper
    
    return decorator


def throttle_method(
    max_calls: int = 1,
    period_seconds: int = 60,
    per_user: bool = True
):
    """
    دکوراتور برای محدود کردن تعداد فراخوانی یک متد
    
    مثال استفاده:
    
        class Customer(models.Model):
            @throttle_method(max_calls=5, period_seconds=300)
            def send_otp(self):
                # فقط ۵ بار در ۵ دقیقه
                pass
    
    Args:
        max_calls: حداکثر تعداد فراخوانی
        period_seconds: بازه زمانی
        per_user: آیا محدودیت بر اساس کاربر باشد؟
    """
    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            # تولید کلید cache
            if per_user and kwargs.get('request'):
                user = kwargs['request'].user
                user_id = user.id if hasattr(user, 'id') else None
                identifier = f'user_{user_id}' if user_id else 'anonymous'
            else:
                identifier = 'global'
            
            cache_key = f'throttle_method:{method.__qualname__}:{identifier}'
            
            # بررسی تعداد فراخوانی
            current = cache.get(cache_key, 0)
            if current >= max_calls:
                raise RateLimitError(
                    message=f'این متد فقط {max_calls} بار در {period_seconds} ثانیه قابل فراخوانی است.'
                )
            
            # افزایش شمارنده
            cache.set(cache_key, current + 1, period_seconds)
            
            # فراخوانی متد
            return method(self, *args, **kwargs)
        
        return wrapper
    
    return decorator


# ============================================================================
# توابع کمکی
# ============================================================================

def _get_client_ip() -> str:
    """
    استخراج IP واقعی مشتری از header‌ها
    
    در صورتی که از Reverse Proxy استفاده شود، IP واقعی 
    از X-Forwarded-For یا similar headers خوانده می‌شود.
    """
    # از request.current استفاده می‌کنیم (اگر موجود باشد)
    try:
        from django.http import HttpRequest
        # این تابع باید از context جداگانه فراخوانی شود
        pass
    except:
        pass
    
    # پیش‌فرض: localhost
    return '127.0.0.1'


def get_rate_limit_headers(
    user: User,
    endpoint: str,
    max_requests: int = DEFAULT_RATE_LIMIT_MAX_REQUESTS,
    window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS
) -> dict:
    """
    دریافت header‌های استاندارد Rate Limiting
    
    این header‌ها در پاسخ API ارسال می‌شوند تا مشتری 
    بتواند وضعیت نرخ را بداند.
    
    Returns:
        Dictionary حاوی header‌های Rate Limiting
    """
    info = get_remaining_requests(user, endpoint, max_requests, window_seconds)
    
    return {
        'X-RateLimit-Limit': max_requests,
        'X-RateLimit-Remaining': info['remaining_requests'],
        'X-RateLimit-Reset': info['reset_at'],
        'X-RateLimit-Window': window_seconds,
    }


def reset_rate_limit(user: User, endpoint: str, ip_address: Optional[str] = None):
    """
    ریست کردن شمارنده نرخ برای یک کاربر
    
    این تابع معمولاً توسط مدیران برای رفع موقت محدودیت استفاده می‌شود.
    
    Args:
        user: کاربر مورد نظر
        endpoint: مسیر API
        ip_address: آدرس IP (اختیاری)
    """
    if not ip_address:
        ip_address = _get_client_ip()
    
    identifier = _generate_identifier(user, ip_address, endpoint)
    cache_key = _generate_cache_key(DEFAULT_RATE_LIMIT_KEY_PREFIX, identifier)
    
    # حذف کلید cache
    client = get_redis_client()
    if client:
        client.delete(cache_key)
    else:
        cache.delete(cache_key)
