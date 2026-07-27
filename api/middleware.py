"""
middleware.py - میان‌افزار احراز هویت مبتنی بر session_log

این میان‌افزار کلید نشست را بررسی و اطلاعات کاربر را به request اضافه می‌کند.
"""

import os

from django.core.cache import cache
from django.db import models
from django.http import JsonResponse
from django.utils import timezone
from profiles.models import session_log, user_profile


class ApiCsrfExemptionMiddleware:
	"""Skip cookie CSRF checks only for token API calls and public auth endpoints.

	The panel authenticates API requests with a Bearer session key, so those requests
	do not rely on ambient browser cookies and are not vulnerable to cookie-based CSRF.
	Cookie-authenticated API calls deliberately remain protected.
	"""

	public_auth_paths = {
		'/api/v1/auth/check-phone/',
		'/api/v1/auth/request-otp/',
		'/api/v1/auth/login-password/',
		'/api/v1/auth/verify-otp/',
		'/api/v1/auth/register/',
		'/api/v1/auth/password-reset/request/',
		'/api/v1/auth/password-reset/confirm/',
	}

	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		authorization = request.headers.get('Authorization', '')
		is_bearer_api = (
			request.path.startswith('/api/v1/')
			and authorization.startswith('Bearer ')
		)
		if request.path in self.public_auth_paths or is_bearer_api:
			request._dont_enforce_csrf_checks = True
		return self.get_response(request)


class AuthenticationMiddleware:
	"""میان‌افزار احراز هویت مبتنی بر session_key"""
	
	def __init__(self, get_response):
		self.get_response = get_response
	
	def __call__(self, request):
		public_paths = [
			'/api/v1/auth/help/',
			'/api/v1/auth/check-phone/',
			'/api/v1/auth/request-otp/',
			'/api/v1/auth/login-password/',
			'/api/v1/auth/verify-otp/',
			'/api/v1/auth/register/',
			'/api/v1/auth/password-reset/request/',
			'/api/v1/auth/password-reset/confirm/',
		]

		request.session_obj = None# همیشه مقداردهی اولیه

		if request.path in public_paths:
			return self.get_response(request)

		# فقط اگر Bearer token وجود داشت، user را override کن
		auth_header = request.headers.get("Authorization", "")
		if auth_header.startswith("Bearer "):
			session_key = auth_header[7:]
			try:
				session = session_log.objects.select_related('user').get(
					session_key=session_key,
					is_active=True
				)
				session.last_activity = timezone.now()
				session.save(update_fields=['last_activity'])
				request.user = session.user
				request.session_obj = session
				self._monitor_request_volume(request, session.user)
			except session_log.DoesNotExist:
				request.user = None

		# اگر Bearer نبود، request.user را دست نمی‌زنیم
		# Django's AuthenticationMiddleware قبلاً آن را تنظیم کرده
		return self.get_response(request)

	def _monitor_request_volume(self, request, user):
		"""Flag and alert once an authenticated account exceeds 100 requests/hour."""
		if not request.path.startswith('/api/'):
			return
		bucket = timezone.now().strftime('%Y%m%d%H')
		key = f'security:hourly-requests:{user.pk}:{bucket}'
		cache.add(key, 0, timeout=3700)
		try:
			count = cache.incr(key)
		except ValueError:
			cache.set(key, 1, timeout=3700)
			count = 1
		if count <= 100 or user.security_flagged:
			return
		reason = 'بیش از ۱۰۰ درخواست API از این حساب در یک ساعت'
		user.security_flagged = True
		user.security_flag_reason = reason
		user.security_flagged_at = timezone.now()
		user.save(update_fields=['security_flagged', 'security_flag_reason', 'security_flagged_at', 'updated_at'])
		self._alert_admins(user, count, reason)

	def _alert_admins(self, flagged_user, count, reason):
		from notifications.models import notification
		admins = user_profile.objects.filter(is_active=True).filter(
			models.Q(is_superuser=True) | models.Q(role__role_type='admin')
		).distinct()
		body = f'هشدار امنیتی: حساب {flagged_user.email or flagged_user.phone or flagged_user.pk} در یک ساعت {count} درخواست ثبت کرده و خودکار فلگ شد.'
		for admin in admins:
			sent = False
			if admin.phone:
				try:
					account_sid = os.getenv('TWILIO_ACCOUNT_SID')
					auth_token = os.getenv('TWILIO_AUTH_TOKEN')
					from_number = os.getenv('TWILIO_FROM_NUMBER')
					if account_sid and auth_token and from_number:
						from twilio.rest import Client
						Client(account_sid, auth_token).messages.create(body=body, from_=from_number, to=admin.phone)
						sent = True
				except Exception:
					sent = False
			notification.objects.create(
				recipient=admin, channel='sms', subject='هشدار امنیتی نرخ درخواست', body=body,
				status='sent' if sent else 'failed', sent_at=timezone.now() if sent else None,
				failed_reason=None if sent else 'درگاه پیامک پیکربندی نیست یا ارسال ناموفق بود',
				priority='urgent', metadata={'flagged_user_id': flagged_user.pk, 'request_count': count, 'reason': reason},
			)
