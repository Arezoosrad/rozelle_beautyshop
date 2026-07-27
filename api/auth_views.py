"""
auth_views.py - احراز هویت با OTP و رمز عبور

این ماژول endpoint‌های احراز هویت را مدیریت می‌کند:
- بررسی شماره موبایل و ارسال OTP
- ورود با رمز عبور یا OTP
- خروج از سیستم
"""

import logging
import os
import secrets
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from urllib.parse import urlencode
from django.http import JsonResponse
from django.core.mail import send_mail
from django.core.validators import EmailValidator
from .views import _error, _parse_json
from rozelle_beautyshop.core.sms import send_sms
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.contrib.auth.hashers import check_password
from django.utils.encoding import force_bytes, force_str
from notifications.services import create_user_notifications
from django.contrib.auth.tokens import default_token_generator
from profiles.models import user_profile, otp_code, session_log
from django.contrib.auth.password_validation import validate_password
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

OTP_EXPIRY_MINUTES = 5
OTP_LENGTH = 6
OTP_RESEND_SECONDS = 60

logger = logging.getLogger(__name__)


# =========================================================
# HELPERS
# =========================================================

def _generate_otp():
	"""تولید کد OTP شش‌رقمی"""
	return ''.join([str(secrets.randbelow(10)) for _ in range(OTP_LENGTH)])

def _generate_session_key():
	"""تولید کلید نشست امن"""
	return secrets.token_urlsafe(32)

def _create_session(user, request):
	"""ایجاد نشست جدید برای کاربر و ثبت در session_log"""
	session_key = _generate_session_key()
	
	# استخراج اطلاعات دستگاه
	user_agent = request.META.get('HTTP_USER_AGENT', '')
	ip_address = _get_client_ip(request)
	
	# ایجاد session_log
	session = session_log.objects.create(
		user=user,
		session_key=session_key,
		ip_address=ip_address,
		user_agent=user_agent,
		device_type=_detect_device_type(user_agent),
		is_active=True,
	)
	
	# به‌روزرسانی last_login
	user.last_login = timezone.now()
	user.save(update_fields=['last_login'])
	
	return session_key, session

def _get_client_ip(request):
	"""استخراج IP واقعی کاربر"""
	x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
	if x_forwarded_for:
		ip = x_forwarded_for.split(',')[0]
	else:
		ip = request.META.get('REMOTE_ADDR')
	return ip

def _detect_device_type(user_agent):
	"""تشخیص نوع دستگاه از User Agent"""
	user_agent = user_agent.lower()
	if 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent:
		return 'mobile'
	elif 'tablet' in user_agent or 'ipad' in user_agent:
		return 'tablet'
	elif 'windows' in user_agent or 'mac' in user_agent or 'linux' in user_agent:
		return 'desktop'
	return 'unknown'

def _send_otp(user, code, method=None):
	"""ارسال OTP با کانال انتخابی بدون افشای کد در خروجی یا log."""
	method = method or user.two_factor_method or 'sms'

	if method == 'email':
		if not user.email:
			return False
		send_mail(
			'کد تایید امن‌نگار پارتاک',
			f'کد یکبارمصرف شما: {code}\nاین کد تا {OTP_EXPIRY_MINUTES} دقیقه معتبر است.',
			settings.DEFAULT_FROM_EMAIL or None,
			[user.email],
			fail_silently=False,
		)
		return True

	if not user.phone:
		return False

	try:
		return send_sms(
			to=user.phone,
			text=code,
			body_id=settings.SMS_BODY_ID_OTP,
		)
	except Exception:
		logger.exception("OTP SMS dispatch failed for user %s.", user.pk)
		return False

def _issue_otp(user, request, purpose='login', method=None):
	method = method or user.two_factor_method or ('email' if user.email else 'sms')
	if method == 'sms' and not user.phone and user.email:
		method = 'email'
	elif method == 'email' and not user.email and user.phone:
		method = 'sms'
	recipient = user.email if method == 'email' else user.phone
	if not recipient:
		raise ValidationError('برای روش انتخاب‌شده اطلاعات تماس ثبت نشده است.')

	# فقط آخرین کد معتبر باشد و درخواست‌های پیاپی محدود شوند.
	last = otp_code.objects.filter(user=user, purpose=purpose).order_by('-created_at').first()
	if last and last.created_at > timezone.now() - timedelta(seconds=OTP_RESEND_SECONDS):
		raise ValidationError('برای ارسال مجدد کد یک دقیقه صبر کنید.')

	code = _generate_otp()
	sent = _send_otp(user, code, method=method)
	if not sent:
		raise ValidationError('ارسال کد تایید انجام نشد. لطفا کمی بعد دوباره تلاش کنید.')

	# فقط بعد از تایید موفق ارسال، کد قبلی را بی‌اعتبار و چالش جدید را ثبت می‌کنیم.
	otp_code.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)
	obj = otp_code.objects.create(
		user=user,
		code=code,
		purpose=purpose,
		recipient=recipient,
		expires_at=timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES),
		ip_address=_get_client_ip(request),
		is_used=False,
	)
	return obj, True, method

def _serialize_user(user):
	"""سریال‌سازی اطلاعات کاربر"""
	return {
		"id": user.id,
		"phone": user.phone,
		"email": user.email,
		"first_name": user.first_name,
		"last_name": user.last_name,
		"full_name": user.get_full_name(),
		"company_name": user.company_name,
		"role": user.role.name if user.role else None,
		"role_fa": user.role.name_fa if user.role else None,
		"is_verified": user.is_verified,
		"is_active": user.is_active,
		"is_staff": user.is_staff,
		"role_type": user.role.role_type if user.role else None,
		"access_level": user.access_level,
		"two_factor_required": user.is_two_factor_required,
		"two_factor_enabled": user.two_factor_enabled,
		"two_factor_forced": user.two_factor_forced,
		"two_factor_method": user.two_factor_method,
		"security_flagged": user.security_flagged,
	}


def _resolve_identifier(identifier):
	"""Normalize an email/phone identifier and return (normalized, type, user)."""
	identifier = str(identifier or '').strip()
	if not identifier:
		raise ValidationError('ایمیل یا شماره موبایل الزامی است')

	if '@' in identifier:
		normalized = identifier.lower()
		EmailValidator()(normalized)
		user = user_profile.objects.filter(email__iexact=normalized).first()
		return normalized, 'email', user

	normalized = user_profile.objects.normalize_phone(identifier)
	if not normalized or len(normalized) != 11 or not normalized.startswith('09'):
		raise ValidationError('شماره موبایل معتبر نیست')
	user = user_profile.objects.filter(phone=normalized).first()
	return normalized, 'phone', user


def _identifier_result(identifier, user, identifier_type):
	if user is None:
		return {
			'user_exists': False,
			'has_password': False,
			'identifier': identifier,
			'identifier_type': identifier_type,
			'next_step': 'register',
			'message': 'این مشخصات در بادی‌یار ثبت نشده است؛ ثبت‌نام را ادامه دهید',
		}

	return {
		'user_exists': True,
		'has_password': bool(user.password and user.has_usable_password()),
		'identifier': identifier,
		'identifier_type': identifier_type,
		'next_step': 'login',
		'message': 'حساب شما پیدا شد',
	}


# =========================================================
# CHECK PHONE
# =========================================================

@require_POST
def check_phone(request):
	"""بررسی شماره موبایل بدون ساختن کاربر؛ کاربر جدید به ثبت‌نام هدایت می‌شود."""
	payload = _parse_json(request)
	if not payload:
		return _error("invalid_json", "فرمت JSON نامعتبر است")

	try:
		identifier, identifier_type, user = _resolve_identifier(payload.get("phone"))
	except ValidationError as exc:
		return _error("invalid_identifier", exc.messages, 400)

	return JsonResponse({
		"ok": True,
		"data": _identifier_result(identifier, user, identifier_type),
	})


@require_POST
def check_identifier(request):
	"""بررسی ایمیل یا شماره موبایل برای تعیین مسیر ورود یا ثبت‌نام."""
	payload = _parse_json(request)
	if not payload:
		return _error("invalid_json", "فرمت JSON نامعتبر است")

	raw_identifier = payload.get("identifier") or payload.get("email") or payload.get("phone")
	try:
		identifier, identifier_type, user = _resolve_identifier(raw_identifier)
	except ValidationError as exc:
		return _error("invalid_identifier", exc.messages, 400)

	return JsonResponse({
		"ok": True,
		"data": _identifier_result(identifier, user, identifier_type),
	})


# =========================================================
# REQUEST OTP
# =========================================================

@require_POST
def request_otp(request):
	"""ارسال OTP برای ایمیل یا شماره موبایل موجود."""
	payload = _parse_json(request)
	if not payload:
		return _error("invalid_json", "فرمت JSON نامعتبر است")

	raw_identifier = payload.get("identifier") or payload.get("email") or payload.get("phone")
	try:
		identifier, identifier_type, user = _resolve_identifier(raw_identifier)
	except ValidationError as exc:
		return _error("invalid_identifier", exc.messages, 400)

	if user is None:
		return _error("user_not_found", "کاربر یافت نشد", 404)

	if not user.is_active:
		# The only inactive account allowed to request another OTP is a freshly
		# registered phone account that still needs phone verification.
		if identifier_type != 'phone' or user.is_verified:
			return _error("user_inactive", "حساب کاربری غیرفعال است", 403)

	try:
		purpose = 'phone_verification' if not user.is_active else 'login'
		challenge, otp_sent, method = _issue_otp(
			user,
			request,
			purpose=purpose,
			method='email' if identifier_type == 'email' else 'sms',
		)
	except ValidationError as exc:
		return _error('otp_not_sent', exc.messages, 400)

	return JsonResponse({
		"ok": True,
		"data": {
			"challenge_id": challenge.pk,
			"otp_sent": otp_sent,
			"method": method,
			"next_step": "verify_otp",
			"message": "کد تایید به ایمیل شما ارسال شد" if method == 'email' else "کد تایید به شماره موبایل شما پیامک شد",
		}
	})


# =========================================================
# LOGIN WITH PASSWORD
# =========================================================

@require_POST
def login_with_password(request):
	"""
	ورود با رمز عبور
	
	ورودی:
	{
		"email": "test@example.com",
		"password": "secret123"
	}
	
	خروجی:
	{
		"ok": true,
		"data": {
			"session_key": "abc123...",
			"user": {...},
			"message": "ورود موفقیت‌آمیز بود"
		}
	}
	"""
	payload = _parse_json(request)
	if not payload:
		return _error("invalid_json", "فرمت JSON نامعتبر است")
	
	identifier = (payload.get("identifier") or payload.get("email") or payload.get("phone") or "").strip()
	password = payload.get("password")
	
	if not identifier or not password:
		return _error("missing_credentials", "ایمیل/شماره موبایل و رمز عبور الزامی است")
	
	# یافتن کاربر
	if '@' in identifier:
		user = user_profile.objects.filter(email__iexact=identifier.lower()).first()
	else:
		phone = user_profile.objects.normalize_phone(identifier)
		user = user_profile.objects.filter(phone=phone).first()
	if user is None:
		return _error("user_not_found", "نام کاربری یافت نشد", 404)
	
	if not user.is_active:
		return _error("user_inactive", "حساب کاربری غیرفعال است", 403)
	
	# بررسی رمز عبور
	if not user.password or not check_password(password, user.password):
		return _error("invalid_credentials", "ایمیل یا رمز عبور اشتباه است", 401)

	if user.is_two_factor_required:
		try:
			challenge, sent, method = _issue_otp(user, request, purpose='login')
		except ValidationError as exc:
			return _error('otp_not_sent', exc.messages, 400)
		return JsonResponse({
			"ok": True,
			"data": {
				"requires_2fa": True,
				"challenge_id": challenge.pk,
				"otp_sent": sent,
				"method": method,
				"next_step": "verify_otp",
			}
		})
	
	# ایجاد نشست
	session_key, session = _create_session(user, request)
	
	return JsonResponse({
		"ok": True,
		"data": {
			"session_key": session_key,
			"user": _serialize_user(user),
			"message": "ورود موفقیت‌آمیز بود"
		}
	})


# =========================================================
# VERIFY OTP
# =========================================================

@require_POST
def verify_otp(request):
	"""
	تایید کد OTP و ورود/ثبت‌نام
	
	ورودی:
	{
		"phone": "09121234567",
		"otp": "123456"
	}
	
	خروجی (کاربر تایید نشده):
	{
		"ok": true,
		"data": {
			"session_key": "abc123...",
			"user": {...},
			"is_new_user": true,
			"next_step": "complete_registration",
			"message": "لطفا ثبت‌نام را تکمیل کنید"
		}
	}
	
	خروجی (کاربر تایید شده):
	{
		"ok": true,
		"data": {
			"session_key": "abc123...",
			"user": {...},
			"is_new_user": false,
			"message": "ورود موفقیت‌آمیز بود"
		}
	}
	"""
	payload = _parse_json(request)
	if not payload:
		return _error("invalid_json", "فرمت JSON نامعتبر است")
	
	otp = payload.get("otp", "").strip()
	challenge_id = payload.get('challenge_id')
	identifier = (payload.get('identifier') or payload.get('email') or payload.get('phone') or '').strip()
	if not otp or (not challenge_id and not identifier):
		return _error("missing_credentials", "شناسه/چالش و کد تایید الزامی است")

	if challenge_id:
		otp_obj = otp_code.objects.select_related('user').filter(
			pk=challenge_id,
			is_used=False,
			purpose__in=['phone_verification', 'login'],
		).first()
		user = otp_obj.user if otp_obj else None
	else:
		if '@' in identifier:
			user = user_profile.objects.filter(email__iexact=identifier.lower()).first()
		else:
			phone = user_profile.objects.normalize_phone(identifier)
			user = user_profile.objects.filter(phone=phone).first()
		otp_obj = otp_code.objects.filter(
			user=user,
			is_used=False,
			purpose__in=['phone_verification', 'login'],
		).order_by('-created_at').first() if user else None

	if user is None or otp_obj is None:
		return _error("invalid_otp", "کد تایید نامعتبر است", 401)
	if otp_obj.expires_at <= timezone.now():
		return _error("expired_otp", "کد تایید منقضی شده است", 401)
	
	# بررسی تعداد تلاش‌ها
	if otp_obj.attempts >= 5:
		return _error("too_many_attempts", "تعداد تلاش‌های شما به حداکثر رسیده است", 429)
	
	# verify روی همان آخرین challenge شمارنده تلاش اشتباه را افزایش می‌دهد.
	if not otp_obj.verify(otp):
		return _error("invalid_otp", f"کد تایید اشتباه است. {5 - otp_obj.attempts} تلاش باقی مانده", 401)
	
	# تایید کاربر (در صورت عدم تایید قبلی)
	is_new_user = not user.is_verified
	if is_new_user and otp_obj.purpose == 'phone_verification':
		user.is_verified = True
		user.is_active = True
		user.save(update_fields=['is_verified', 'is_active'])

	if not user.is_active:
		return JsonResponse({
			"ok": True,
			"data": {
				"session_key": None,
				"user": _serialize_user(user),
				"is_new_user": is_new_user,
				"next_step": "awaiting_activation",
				"message": "حساب تایید شد و پس از فعال‌سازی مدیر قابل ورود است",
			}
		})
	
	# ایجاد نشست
	session_key, session = _create_session(user, request)
	
	return JsonResponse({
		"ok": True,
		"data": {
			"session_key": session_key,
			"user": _serialize_user(user),
			"is_new_user": is_new_user,
			"next_step": "complete_registration" if is_new_user else None,
			"message": "لطفا ثبت‌نام را تکمیل کنید" if is_new_user else "ورود موفقیت‌آمیز بود"
		}
	})


# =========================================================
# VERIFY email
# =========================================================

@require_POST
def verify_email(request):
	payload = _parse_json(request) or {}

	user_id = payload.get('uid')
	token = (payload.get('token') or '').strip()

	if not user_id or not token:
		return _error(
			'missing_verification_data',
			'شناسه کاربر و توکن تأیید الزامی هستند',
		)

	result = user_profile.verify_email_token(
		user_id=user_id,
		token=token,
	)

	if not result['success']:
		return _error(
			'invalid_email_token',
			result['message'],
			400,
		)

	return JsonResponse({
		'ok': True,
		'data': {
			'email_verified': True,
			'is_active': result['user'].is_active,
			'next_step': (
				'awaiting_activation'
				if not result['user'].is_active
				else 'login'
			),
			'message': result['message'],
		},
	})

# =========================================================
# EMAIL REGISTRATION / PASSWORD RESET
# =========================================================

@require_POST
def register(request):
	payload = _parse_json(request)
	if not payload:
		return _error('invalid_json', 'فرمت JSON نامعتبر است')

	email = (payload.get('email') or '').strip().lower() or None
	raw_phone = (payload.get('phone') or '').strip()
	password = payload.get('password')
	confirm = payload.get('confirm_password')

	if not email and not raw_phone:
		return _error('missing_identifier', 'ایمیل یا شماره موبایل الزامی است')
	if email:
		try:
			EmailValidator()(email)
		except ValidationError as exc:
			return _error('invalid_email', exc.messages, 400)

	phone = None
	if raw_phone:
		try:
			phone = user_profile.objects.normalize_phone(raw_phone)
			if not phone or len(phone) != 11 or not phone.startswith('09'):
				raise ValidationError('شماره موبایل معتبر نیست')
		except ValidationError as exc:
			return _error('invalid_phone', exc.messages, 400)

	if not password:
		return _error('missing_credentials', 'رمز عبور الزامی است')
	if confirm is not None and password != confirm:
		return _error('password_mismatch', 'رمز عبور و تکرار آن یکسان نیستند')

	if email and user_profile.objects.filter(email__iexact=email).exists():
		return _error('email_exists', 'این ایمیل قبلاً ثبت شده است', 409)
	if phone and user_profile.objects.filter(phone=phone).exists():
		return _error('phone_exists', 'این شماره موبایل قبلاً ثبت شده است', 409)

	try:
		validate_password(password)
	except ValidationError as exc:
		return _error('invalid_password', exc.messages, 400)

	user = user_profile(
		email=email,
		phone=phone,
		first_name=(payload.get('first_name') or '').strip() or None,
		last_name=(payload.get('last_name') or '').strip() or None,
		is_active=False,
		is_verified=False,
		is_email_verified=False,
	)
	user.set_password(password)
	user.save()

	if phone:
		try:
			challenge, _, _ = _issue_otp(
				user,
				request,
				purpose='phone_verification',
				method='sms',
			)
		except ValidationError as exc:
			user.delete()
			return _error('otp_not_sent', exc.messages, 400)

		return JsonResponse({
			'ok': True,
			'data': {
				'user': _serialize_user(user),
				'otp_sent': True,
				'challenge_id': challenge.pk,
				'next_step': 'verify_otp',
				'message': 'ثبت‌نام انجام شد. کد تایید به شماره موبایل شما پیامک شد.',
			},
		}, status=201)

	# Email registration continues through the existing email-verification flow.
	email_verification_token = secrets.token_urlsafe(32)
	user.notes = email_verification_token
	user.save(update_fields=['notes', 'updated_at'])

	frontend_base = os.getenv(
		'FRONTEND_BASE_URL',
		'https://crm.amn-negar.com',
	).rstrip('/')

	query_string = urlencode({'uid': user.pk, 'token': email_verification_token})
	verify_url = f'{frontend_base}/verify-email/?{query_string}'

	send_mail(
		subject='تأیید ایمیل بادی‌یار',
		message=(
			f'سلام {user.get_full_name()}،\n\n'
			f'برای تأیید آدرس ایمیل خود روی لینک زیر کلیک کنید:\n\n'
			f'{verify_url}\n\n'
			'اگر شما این درخواست را ارسال نکرده‌اید، این ایمیل را نادیده بگیرید.'
		),
		from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
		recipient_list=[user.email],
		fail_silently=False,
	)

	return JsonResponse({
		'ok': True,
		'data': {
			'user': _serialize_user(user),
			'email_verification_sent': True,
			'next_step': 'verify_email',
			'message': 'ثبت‌نام انجام شد. لطفاً ایمیل خود را برای تأیید حساب بررسی کنید.',
		},
	}, status=201)

@require_POST
def request_password_reset(request):
	payload = _parse_json(request) or {}
	email = (payload.get('email') or '').strip().lower()
	if not email:
		return _error('missing_email', 'ایمیل الزامی است')
	user = user_profile.objects.filter(email__iexact=email, is_active=True).first()
	if user:
		uid = urlsafe_base64_encode(force_bytes(user.pk))
		token = default_token_generator.make_token(user)
		frontend_base = os.getenv('FRONTEND_BASE_URL', 'https://crm.amn-negar.com').rstrip('/')
		reset_url = f'{frontend_base}/reset-password?uid={uid}&token={token}'
		send_mail(
			'بازیابی رمز عبور امن‌نگار پارتاک',
			f'برای تنظیم رمز عبور جدید این پیوند را باز کنید:\n{reset_url}',
			settings.DEFAULT_FROM_EMAIL or None,
			[user.email],
			fail_silently=False,
		)
		# اطلاع‌رسانی پیامکی بازیابی گذرواژه (پترن 520736)
		if user.phone:
			create_user_notifications(
				recipient=user,
				subject='بازیابی گذرواژه',
				body='کد بازیابی گذرواژه ارسال شد.',
				event_type='forgot_password',
				metadata={'code': token},
			)
	return JsonResponse({'ok': True, 'data': {'message': 'اگر حساب فعالی با این ایمیل وجود داشته باشد، لینک بازیابی ارسال می‌شود.'}})

@require_POST
def confirm_password_reset(request):
	payload = _parse_json(request) or {}
	uid = payload.get('uid')
	token = payload.get('token')
	password = payload.get('password') or payload.get('new_password')
	confirm = payload.get('confirm_password')
	if not uid or not token or not password:
		return _error('missing_fields', 'شناسه، توکن و رمز جدید الزامی هستند')
	try:
		user_id = force_str(urlsafe_base64_decode(uid))
		user = user_profile.objects.get(pk=user_id, is_active=True)
	except (ValueError, TypeError, OverflowError, user_profile.DoesNotExist):
		return _error('invalid_reset_link', 'لینک بازیابی نامعتبر است', 400)
	if not default_token_generator.check_token(user, token):
		return _error('invalid_reset_link', 'لینک بازیابی نامعتبر یا منقضی است', 400)
	if confirm is not None and password != confirm:
		return _error('password_mismatch', 'رمز عبور و تکرار آن یکسان نیستند')
	try:
		validate_password(password, user=user)
	except ValidationError as exc:
		return _error('invalid_password', exc.messages, 400)
	user.set_password(password)
	user.save(update_fields=['password'])
	session_log.objects.filter(user=user, is_active=True).update(is_active=False, logout_at=timezone.now())

	# اطلاع‌رسانی تغییر گذرواژه (پترن 520740)
	identifier = user.phone or user.email or str(user.pk)
	create_user_notifications(
		recipient=user,
		subject='فعال‌سازی حساب',
		body=f'حساب شما به شناسه {identifier} در سامانه امن‌نگار فعال شد.',
		event_type='activate_account',
		metadata={'identifier': identifier},
	)

	return JsonResponse({'ok': True, 'data': {'password_reset': True, 'next_step': 'login'}})


def _authenticated_user(request):
	user = getattr(request, 'user', None)
	return user if user and getattr(user, 'is_authenticated', False) else None


@require_POST
def request_two_factor_setup(request):
	user = _authenticated_user(request)
	if user is None:
		return _error('missing_session_key', 'نشست معتبر لازم است', 401)
	payload = _parse_json(request) or {}
	method = payload.get('method') or user.two_factor_method or 'sms'
	if method not in {'sms', 'email'}:
		return _error('invalid_method', 'روش دومرحله‌ای باید پیامک یا ایمیل باشد', 400)
	try:
		challenge, sent, method = _issue_otp(user, request, purpose='two_factor_setup', method=method)
	except ValidationError as exc:
		return _error('otp_not_sent', exc.messages, 400)
	return JsonResponse({'ok': True, 'data': {
		'challenge_id': challenge.pk, 'otp_sent': sent, 'method': method, 'expires_in': 300,
	}})


@require_POST
def confirm_two_factor_setup(request):
	user = _authenticated_user(request)
	if user is None:
		return _error('missing_session_key', 'نشست معتبر لازم است', 401)
	payload = _parse_json(request) or {}
	challenge = otp_code.objects.filter(
		pk=payload.get('challenge_id'), user=user, purpose='two_factor_setup', is_used=False,
	).first()
	if challenge is None or challenge.expires_at <= timezone.now():
		return _error('expired_otp', 'کد نامعتبر یا منقضی است', 400)
	if challenge.attempts >= 5:
		return _error('too_many_attempts', 'حداکثر تلاش انجام شده است', 429)
	if not challenge.verify(str(payload.get('otp') or '').strip()):
		return _error('invalid_otp', 'کد تایید نادرست است', 400)
	method = 'email' if challenge.recipient == user.email else 'sms'
	user.two_factor_method = method
	user.two_factor_enabled = True
	user.save(update_fields=['two_factor_method', 'two_factor_enabled', 'updated_at'])
	return JsonResponse({'ok': True, 'data': {'enabled': True, 'method': method}})


@require_POST
def disable_two_factor(request):
	user = _authenticated_user(request)
	if user is None:
		return _error('missing_session_key', 'نشست معتبر لازم است', 401)
	if user.two_factor_forced or getattr(user.role, 'two_factor_required', False):
		return _error('two_factor_forced', 'ورود دومرحله‌ای برای نقش یا حساب شما اجباری است', 409)
	payload = _parse_json(request) or {}
	if not payload.get('current_password') or not user.check_password(payload['current_password']):
		return _error('invalid_credentials', 'رمز عبور فعلی نادرست است', 401)
	user.two_factor_enabled = False
	user.save(update_fields=['two_factor_enabled', 'updated_at'])
	return JsonResponse({'ok': True, 'data': {'enabled': False}})


# =========================================================
# LOGOUT
# =========================================================

@require_POST
def logout(request):
	"""
	خروج از حساب کاربری
	
	هدر:
	Authorization: Bearer <session_key>
	
	خروجی:
	{
		"ok": true,
		"data": {
			"logged_out": true,
			"message": "از حساب کاربری خارج شدید"
		}
	}
	"""
	auth_header = request.headers.get("Authorization", "")
	
	if not auth_header.startswith("Bearer "):
		return _error("missing_session_key", "کلید نشست الزامی است", 401)
	
	session_key = auth_header[7:]  # حذف "Bearer "
	
	try:
		session = session_log.objects.get(
			session_key=session_key,
			is_active=True
		)
		session.terminate()
		
		return JsonResponse({
			"ok": True,
			"data": {
				"logged_out": True,
				"message": "از حساب کاربری خارج شدید"
			}
		})
	
	except session_log.DoesNotExist:
		return _error("invalid_session", "نشست نامعتبر است", 401)


# =========================================================
# AUTH HELP - راهنمای کامل احراز هویت
# =========================================================

def auth_help(request):
	"""
	راهنمای کامل سیستم احراز هویت
	
	این endpoint تمام مراحل، endpoint‌ها، ورودی‌ها، خروجی‌ها و فلوی
	احراز هویت را به صورت ساختاریافته برای فرانت برمی‌گرداند.
	
	فراخوانی:
	GET /api/v1/auth/help/
	"""
	
	base_url = "/api/v1/auth"
	
	return JsonResponse({
		"ok": True,
		"data": {
			"title": "راهنمای سیستم احراز هویت",
			"version": "1.0",
			"base_url": base_url,
			"auth_method": "Bearer Token (session_key)",
			"auth_header": "Authorization: Bearer <session_key>",
			
			# ============================================================
			# جریان کلی احراز هویت
			# ============================================================
			"flow": {
				"description": "جریان احراز هویت بر اساس ایمیل یا شماره موبایل",
				"steps": [
					{
						"step": 1,
						"action": "بررسی شماره موبایل",
						"endpoint": "check-phone",
						"description": "همیشه اولین قدم. وضعیت کاربر و مرحله بعدی را مشخص می‌کند."
					},
					{
						"step": 2,
						"action": "بر اساس next_step تصمیم‌گیری کنید",
						"branches": {
							"verify_otp": "کد تایید ارسال شده است. فرم OTP را نمایش دهید.",
							"login": "کاربر موجود است. می‌توانید ورود با رمز عبور یا OTP را انتخاب کنید.",
							"request_otp": "برای ورود با OTP، request-otp را فراخوانی کنید.",
							"register": "کاربر جدید است. به صفحه ثبت‌نام بروید."
						}
					},
					{
						"step": 3,
						"action": "ورود نهایی",
						"description": "session_key دریافتی را ذخیره و در تمام درخواست‌های بعدی استفاده کنید."
					}
				]
			},
			
			# ============================================================
			# جزئیات تمام endpoint‌ها
			# ============================================================
			"endpoints": [
				
				# ---------------- CHECK PHONE ----------------
				{
					"name": "بررسی شماره موبایل",
					"endpoint": f"{base_url}/check-phone/",
					"method": "POST",
					"auth_required": False,
					"description": "سازگاری با کلاینت‌های قدیمی برای بررسی شماره موبایل؛ کاربر جدید ایجاد نمی‌شود و در صورت جدید بودن، مسیر ثبت‌نام برگردانده می‌شود.",
					"request": {
						"headers": {
							"Content-Type": "application/json"
						},
						"body": {
							"phone": {
								"type": "string",
								"required": True,
								"description": "شماره موبایل (هر فرمتی - نرمال‌سازی می‌شود)",
								"example": "09121234567"
							}
						}
					},
					"responses": {
						"new_user": {
							"description": "کاربر جدید - به ثبت‌نام هدایت شود",
							"example": {
								"ok": True,
								"data": {
									"user_exists": False,
									"next_step": "register",
									"message": "این مشخصات در بادی‌یار ثبت نشده است؛ ثبت‌نام را ادامه دهید"
								}
							}
						},
						"existing_with_password": {
							"description": "کاربر موجود با رمز عبور",
							"example": {
								"ok": True,
								"data": {
									"user_exists": True,
									"has_password": True,
									"next_step": "login",
									"message": "لطفا رمز عبور خود را وارد کنید"
								}
							}
						},
						"existing_without_password": {
							"description": "کاربر موجود بدون رمز عبور",
							"example": {
								"ok": True,
								"data": {
									"user_exists": True,
									"has_password": False,
									"next_step": "request_otp",
									"message": "لطفا ورود با کد تایید را انتخاب کنید"
								}
							}
						}
					},
					"errors": [
						{"code": "invalid_identifier", "message": "شناسه واردشده معتبر نیست", "status": 400},
						{"code": "invalid_json", "message": "فرمت JSON نامعتبر است", "status": 400}
					]
				},
				
				# ---------------- CHECK IDENTIFIER ----------------
				{
					"name": "بررسی ایمیل یا شماره موبایل",
					"endpoint": f"{base_url}/check-identifier/",
					"method": "POST",
					"auth_required": False,
					"description": "تعیین می‌کند شناسه موجود است و باید وارد شود یا کاربر جدید است و باید ثبت‌نام کند.",
					"request": {
						"headers": {"Content-Type": "application/json"},
						"body": {
							"identifier": {
								"type": "string",
								"required": True,
								"description": "ایمیل یا شماره موبایل",
								"example": "09121234567"
							}
						}
					},
					"responses": {
						"existing": {
							"example": {
								"ok": True,
								"data": {
									"user_exists": True,
									"has_password": True,
									"identifier_type": "phone",
									"next_step": "login"
								}
							}
						},
						"new": {
							"example": {
								"ok": True,
								"data": {
									"user_exists": False,
									"identifier_type": "email",
									"next_step": "register"
								}
							}
						}
					}
				},

				# ---------------- REQUEST OTP ----------------
				{
					"name": "درخواست کد تایید",
					"endpoint": f"{base_url}/request-otp/",
					"method": "POST",
					"auth_required": False,
					"description": "برای ارسال OTP ورود و ارسال مجدد OTP به ایمیل یا شماره موبایل استفاده می‌شود.",
					"request": {
						"headers": {
							"Content-Type": "application/json"
						},
						"body": {
							"identifier": {
								"type": "string",
								"required": True,
								"description": "ایمیل یا شماره موبایل کاربر",
								"example": "09121234567"
							}
						}
					},
					"responses": {
						"success": {
							"description": "کد تایید ارسال شد",
							"example": {
								"ok": True,
								"data": {
									"otp_sent": True,
									"next_step": "verify_otp",
									"message": "کد تایید به شماره شما ارسال شد"
								}
							}
						}
					},
					"errors": [
						{"code": "missing_phone", "message": "شماره موبایل الزامی است", "status": 400},
						{"code": "user_not_found", "message": "کاربر یافت نشد", "status": 404},
						{"code": "user_inactive", "message": "حساب کاربری غیرفعال است", "status": 403}
					]
				},
				
				# ---------------- LOGIN WITH PASSWORD ----------------
				{
					"name": "ورود با رمز عبور",
					"endpoint": f"{base_url}/login-password/",
					"method": "POST",
					"auth_required": False,
					"description": "ورود کاربران موجود با رمز عبور. در صورت موفقیت session_key برمی‌گرداند.",
					"request": {
						"headers": {
							"Content-Type": "application/json"
						},
						"body": {
							"phone": {
								"type": "string",
								"required": True,
								"example": "09121234567"
							},
							"password": {
								"type": "string",
								"required": True,
								"example": "secret123"
							}
						}
					},
					"responses": {
						"success": {
							"description": "ورود موفق",
							"example": {
								"ok": True,
								"data": {
									"session_key": "xY3kL9...",
									"user": {
										"id": 1,
										"phone": "09121234567",
										"email": "user@example.com",
										"first_name": "علی",
										"last_name": "محمدی",
										"full_name": "علی محمدی",
										"company_name": "شرکت نمونه",
										"role": "expert",
										"role_fa": "کارشناس",
										"is_verified": True,
										"is_active": True,
										"is_staff": False,
										"wallet_balance": 100000
									},
									"message": "ورود موفقیت‌آمیز بود"
								}
							}
						}
					},
					"errors": [
						{"code": "missing_credentials", "message": "شماره موبایل و رمز عبور الزامی است", "status": 400},
						{"code": "invalid_credentials", "message": "شماره موبایل یا رمز عبور اشتباه است", "status": 401},
						{"code": "user_inactive", "message": "حساب کاربری غیرفعال است", "status": 403}
					]
				},
				
				# ---------------- VERIFY OTP ----------------
				{
					"name": "تایید کد OTP",
					"endpoint": f"{base_url}/verify-otp/",
					"method": "POST",
					"auth_required": False,
					"description": "تایید کد OTP و ورود/تکمیل ثبت‌نام. در صورت موفقیت session_key برمی‌گرداند. اگر کاربر جدید باشد، is_new_user=true و باید به صفحه تکمیل ثبت‌نام هدایت شود.",
					"request": {
						"headers": {
							"Content-Type": "application/json"
						},
						"body": {
							"phone": {
								"type": "string",
								"required": True,
								"example": "09121234567"
							},
							"otp": {
								"type": "string",
								"required": True,
								"description": "کد ۶ رقمی دریافتی",
								"example": "123456"
							}
						}
					},
					"responses": {
						"new_user": {
							"description": "کاربر جدید - باید ثبت‌نام تکمیل شود",
							"example": {
								"ok": True,
								"data": {
									"session_key": "xY3kL9...",
									"user": {"id": 5, "phone": "09121234567"},
									"is_new_user": True,
									"next_step": "complete_registration",
									"message": "لطفا ثبت‌نام را تکمیل کنید"
								}
							}
						},
						"existing_user": {
							"description": "کاربر موجود - ورود موفق",
							"example": {
								"ok": True,
								"data": {
									"session_key": "xY3kL9...",
									"user": {"id": 1, "phone": "09121234567"},
									"is_new_user": False,
									"next_step": None,
									"message": "ورود موفقیت‌آمیز بود"
								}
							}
						}
					},
					"errors": [
						{"code": "missing_credentials", "message": "شماره موبایل و کد تایید الزامی است", "status": 400},
						{"code": "user_not_found", "message": "کاربر یافت نشد", "status": 404},
						{"code": "invalid_otp", "message": "کد تایید نامعتبر یا منقضی شده است", "status": 401},
						{"code": "too_many_attempts", "message": "تعداد تلاش‌های شما به حداکثر رسیده است", "status": 429}
					]
				},
				
				# ---------------- LOGOUT ----------------
				{
					"name": "خروج از حساب",
					"endpoint": f"{base_url}/logout/",
					"method": "POST",
					"auth_required": True,
					"description": "خروج از حساب و غیرفعال‌سازی نشست فعلی.",
					"request": {
						"headers": {
							"Authorization": "Bearer <session_key>"
						},
						"body": {}
					},
					"responses": {
						"success": {
							"description": "خروج موفق",
							"example": {
								"ok": True,
								"data": {
									"logged_out": True,
									"message": "از حساب کاربری خارج شدید"
								}
							}
						}
					},
					"errors": [
						{"code": "missing_session_key", "message": "کلید نشست الزامی است", "status": 401},
						{"code": "invalid_session", "message": "نشست نامعتبر است", "status": 401}
					]
				}
			],
			
			# ============================================================
			# اطلاعات تکمیلی
			# ============================================================
			"notes": {
				"otp": {
					"length": 6,
					"expiry_minutes": 5,
					"max_attempts": 5,
					"description": "کد OTP شش‌رقمی است، ۵ دقیقه اعتبار دارد و حداکثر ۵ تلاش مجاز است."
				},
				"session": {
					"storage": "session_key را در localStorage یا cookie امن ذخیره کنید.",
					"usage": "در تمام درخواست‌های نیازمند احراز هویت، هدر Authorization: Bearer <session_key> ارسال کنید.",
					"description": "هر بار فعالیت، last_activity نشست به‌روز می‌شود."
				},
				"phone_format": "شماره موبایل در هر فرمتی پذیرفته و به فرمت 09xxxxxxxxx نرمال‌سازی می‌شود (98، 0098، 9 نیز پشتیبانی می‌شوند)."
			},
			
			# ============================================================
			# نمونه فلوی کامل
			# ============================================================
			"example_flow": [
				"1. POST /auth/check-phone/ با body: {phone: '09121234567'}",
				"2. بررسی next_step در پاسخ:",
				"   - اگر 'verify_otp': OTP ارسال شده، فرم کد تایید نمایش بده",
				"   - اگر 'login': فرم رمز عبور نمایش بده و POST /auth/login-password/",
				"   - اگر 'request_otp': POST /auth/request-otp/ سپس فرم کد تایید",
				"3. POST /auth/verify-otp/ یا /auth/login-password/ → دریافت session_key",
				"4. ذخیره session_key و استفاده در هدر Authorization تمام درخواست‌ها",
				"5. POST /auth/logout/ برای خروج"
			]
		}
	})
