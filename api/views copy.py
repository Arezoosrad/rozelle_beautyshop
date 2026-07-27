import uuid
import json
import inspect
import logging
import traceback
from ..rozelle_beautyshop.api.constants import *
from ..rozelle_beautyshop.api.permissions import *
from dateutil import parser
from functools import wraps
from django.apps import apps
from django.conf import settings
from django.db import transaction
from datetime import datetime, date
from ..rozelle_beautyshop.api.registry import model_registry
from django.http import JsonResponse
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional, Set
from django.db.models.fields.files import FieldFile
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator, EmptyPage
from ..rozelle_beautyshop.api.rate_limit import check_rate_limit, RateLimitError 
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import CASCADE, PROTECT, SET_NULL, SET_DEFAULT, DO_NOTHING, SET, RESTRICT, Model, QuerySet, Q, Prefetch
logger = logging.getLogger(__name__)

# ============================================================================
# دکوراتورهای کمکی
# ============================================================================

def api_response(success: bool, data: Any = None, error: Dict = None, 
				status: int = 200) -> JsonResponse:
	"""
	ساخت پاسخ یکنواخت API
	
	Args:
		success: وضعیت موفقیت
		data: داده‌های پاسخ (در صورت موفقیت)
		error: جزئیات خطا (در صورت شکست)
		status: کد وضعیت HTTP
	
	Returns:
		JsonResponse با ساختار استاندارد
	"""
	response_data = {'success': success}
	
	if success:
		if data is not None:
			response_data.update(data)
	else:
		response_data['error'] = error or {
			'code': 'UNKNOWN_ERROR',
			'message': 'خطای نامشخص رخ داده است.',
			'details': {}
		}
	
	return JsonResponse(response_data, status=status, json_dumps_params={'ensure_ascii': False})


def handle_api_errors(view_func):
	"""
	دکوراتور برای مدیریت خطاهای رایج API
	
	در حالت DEBUG جزئیات کامل خطا (شامل traceback) برگردانده می‌شود.
	در حالت production فقط پیام عمومی برگردانده می‌شود.
	"""
	@wraps(view_func)
	def wrapper(request, *args, **kwargs):
		try:
			return view_func(request, *args, **kwargs)
		
		except json.JSONDecodeError:
			return api_response(
				success=False,
				error={
					'code': ERROR_CODES['INVALID_INPUT'],
					'message': 'فرمت JSON نامعتبر است.',
					'details': {}
				},
				status=400
			)
		
		except ValueError as e:
			return api_response(
				success=False,
				error={
					'code': ERROR_CODES['INVALID_INPUT'],
					'message': str(e),
					'details': {}
				},
				status=400
			)
		
		except PermissionError as e:
			return api_response(
				success=False,
				error={
					'code': ERROR_CODES['PERMISSION_DENIED'],
					'message': str(e),
					'details': {}
				},
				status=403
			)
		
		except Exception as e:
			
			logger.exception('error on API')
			
			# در حالت DEBUG جزئیات کامل را نمایش می‌دهیم
			if settings.DEBUG:
				error_payload = {
					'code': ERROR_CODES['INTERNAL_ERROR'],
					'message': str(e),
					'details': {
						'exception_type': type(e).__name__,
						'traceback': traceback.format_exc().splitlines(),
					}
				}
			else:
				error_payload = {
					'code': ERROR_CODES['INTERNAL_ERROR'],
					'message': 'خطای داخلی سرور رخ داده است.',
					'details': {}
				}
			
			return api_response(
				success=False,
				error=error_payload,
				status=500
			)
	
	return wrapper



def conditional_auth(view_func):
	"""
	دکوراتور احراز هویت شرطی
	
	برای GET احراز هویت اختیاری است (ممکن است کاربر مهمان باشد)
	برای POST احراز هویت الزامی است
	"""
	@wraps(view_func)
	def wrapper(request, *args, **kwargs):
		if request.method == 'POST' and not request.user.is_authenticated:
			return api_response(
				success=False,
				error={
					'code': ERROR_CODES['UNAUTHENTICATED'],
					'message': 'برای این عملیات باید وارد شوید.',
					'details': {}
				},
				status=401
			)
		return view_func(request, *args, **kwargs)
	
	return wrapper


# ============================================================================
# توابع کمکی برای پردازش درخواست
# ============================================================================

def parse_fields_param(fields_str: Optional[str], allowed_fields: Set[str]) -> List[str]:
	"""
	پردازش پارامتر fields و اعتبارسنجی آن
	
	Args:
		fields_str: رشته فیلدهای درخواستی (با کاما جدا شده)
		allowed_fields: مجموعه فیلدهای مجاز برای کاربر
	
	Returns:
		لیست فیلدهای معتبر و مجاز
	
	Raises:
		ValueError: اگر تعداد فیلدها بیش از حد مجاز باشد
	"""
	if not fields_str or fields_str.lower() == 'all':
		return list(allowed_fields)
	
	requested_fields = [f.strip() for f in fields_str.split(',') if f.strip()]
	
	# بررسی سقف تعداد فیلدها
	if len(requested_fields) > MAX_FIELDS_PER_REQUEST:
		raise ValueError(
			f'تعداد فیلدهای درخواستی ({len(requested_fields)}) '
			f'بیش از حد مجاز ({MAX_FIELDS_PER_REQUEST}) است.'
		)
	
	# فیلتر کردن فیلدهای مجاز (بی‌صدا فیلدهای غیرمجاز را حذف می‌کنیم)
	valid_fields = [f for f in requested_fields if f in allowed_fields]
	
	# اگر هیچ فیلد معتبری نبود، حداقل id را برمی‌گردانیم
	if not valid_fields and 'id' in allowed_fields:
		valid_fields = ['id']
	
	return valid_fields


def parse_ordering_param(ordering_str: Optional[str], allowed_fields: Set[str]) -> List[str]:
	"""
	پردازش پارامتر ordering و اعتبارسنجی آن
	
	Args:
		ordering_str: رشته فیلدهای مرتب‌سازی (با کاما جدا، - برای نزولی)
		allowed_fields: مجموعه فیلدهای مجاز
	
	Returns:
		لیست فیلدهای مرتب‌سازی معتبر
	"""
	if not ordering_str:
		return ['-id']  # پیش‌فرض: جدیدترین‌ها اول
	
	ordering_fields = []
	for field in ordering_str.split(','):
		field = field.strip()
		if not field:
			continue
		
		# بررسی جهت مرتب‌سازی
		if field.startswith('-'):
			field_name = field[1:]
			direction = '-'
		else:
			field_name = field
			direction = ''
		
		# فقط فیلدهای مجاز را قبول می‌کنیم
		if field_name in allowed_fields:
			ordering_fields.append(f'{direction}{field_name}')
	
	return ordering_fields or ['-id']


def optimize_queryset_for_fields(queryset, fields: List[str], model_config: Dict) -> Any:
	"""
	بهینه‌سازی queryset بر اساس فیلدهای درخواستی (جلوگیری از N+1)
	
	این تابع روابط ForeignKey و ManyToMany را شناسایی کرده و 
	با select_related یا prefetch_related بارگذاری می‌کند.
	
	Args:
		queryset: QuerySet اولیه
		fields: لیست فیلدهای درخواستی
		model_config: تنظیمات مدل از رجیستری
	
	Returns:
		QuerySet بهینه‌شده
	"""
	model = model_config['model']
	select_related_fields = []
	prefetch_related_fields = []
	
	for field_name in fields:
		try:
			field = model._meta.get_field(field_name)
			
			# ForeignKey یا OneToOneField → select_related
			if field.many_to_one or field.one_to_one:
				select_related_fields.append(field_name)
			
			# ManyToManyField یا reverse ForeignKey → prefetch_related
			elif field.many_to_many or field.one_to_many:
				prefetch_related_fields.append(field_name)
		
		except Exception:
			# اگر فیلد یک property یا متد باشد، نادیده می‌گیریم
			continue
	
	if select_related_fields:
		queryset = queryset.select_related(*select_related_fields)
	
	if prefetch_related_fields:
		queryset = queryset.prefetch_related(*prefetch_related_fields)
	
	return queryset


def serialize_object(obj, fields: List[str]) -> Dict[str, Any]:
	"""
	تبدیل یک آبجکت مدل به dictionary
	
	این تابع هم فیلدها و هم متدهای read-only را پشتیبانی می‌کند.
	
	Args:
		obj: نمونه مدل
		fields: لیست فیلدهایی که باید سریالایز شوند
	
	Returns:
		Dictionary حاوی مقادیر فیلدها
	"""
	data = {}
	
	for field_name in fields:
		try:
			# ابتدا سعی می‌کنیم به عنوان attribute دسترسی پیدا کنیم
			value = getattr(obj, field_name, None)
			
			# اگر مقدار callable است (متد یا property)، آن را صدا می‌زنیم
			if callable(value):
				value = value()
			
			# تبدیل انواع خاص به فرمت JSON-serializable
			if hasattr(value, 'isoformat'):  # datetime objects
				value = value.isoformat()
			elif hasattr(value, 'pk'):  # related objects
				value = value.pk
			elif hasattr(value, 'all'):  # QuerySet یا Manager
				value = [item.pk for item in value.all()]
			
			data[field_name] = value
		
		except Exception:
			# اگر دسترسی به فیلد با مشکل مواجه شد، None قرار می‌دهیم
			data[field_name] = None
	
	return data


# ============================================================================
# View اصلی: لیست و ایجاد
# ============================================================================

@require_http_methods(['GET', 'POST'])
@conditional_auth
@handle_api_errors
def model_list_or_create(request, model_name: str):
	"""
	View اصلی برای لیست کردن و ایجاد آیتم‌های یک مدل
	
	GET: دریافت لیست آیتم‌ها با پشتیبانی از فیلتر، صفحه‌بندی و شمارش
	POST: ایجاد آیتم جدید
	
	Args:
		request: شیء HttpRequest
		model_name: نام مدل (مثل 'customer', 'product')
	
	Returns:
		JsonResponse با ساختار استاندارد
	"""
	
	# ============================================================================
	# مرحله ۱: بررسی وجود مدل در رجیستری
	# ============================================================================
	
	model_config = model_registry.get(model_name.lower())

	if not model_config:
		return api_response(
			success=False,
			error={
				'code': ERROR_CODES['MODEL_NOT_FOUND'],
				'message': f'مدل "{model_name}" در رجیستری یافت نشد.',
				'details': {}
			},
			status=404
		)
	
	model = model_config['model']
	print(model)
	# ============================================================================
	# مرحله ۲: بررسی مجوز در سطح مدل
	# ============================================================================
	
	if request.method == 'GET':
		required_permission = 'view'
	else:  # POST
		required_permission = 'add'
	
	if not check_model_permission(request.user, model, required_permission):
		return api_response(
			success=False,
			error={
				'code': ERROR_CODES['PERMISSION_DENIED'],
				'message': f'شما مجوز {required_permission} این مدل را ندارید.',
				'details': {}
			},
			status=403
		)
	
	# ============================================================================
	# مرحله ۳: مسیریابی به تابع مناسب
	# ============================================================================
	
	if request.method == 'GET':
		return handle_list_request(request, model_name, model_config)
	else:  # POST
		return handle_create_request(request, model_name, model_config)


# ============================================================================
# توابع کمکی برای مدیریت درخواست‌های GET و POST
# ============================================================================

def handle_list_request(request, model_name: str, model_config: Dict) -> JsonResponse:
	"""
	مدیریت درخواست GET برای دریافت لیست آیتم‌ها
	
	این تابع موارد زیر را پشتیبانی می‌کند:
	- انتخاب فیلدهای خاص (fields)
	- صفحه‌بندی (page, page_size)
	- شمارش سریع (count=true)
	- مرتب‌سازی (ordering)
	- بهینه‌سازی خودکار کوئری‌ها
	
	Args:
		request: شیء HttpRequest
		model_name: نام مدل
		model_config: تنظیمات مدل از رجیستری
	
	Returns:
		JsonResponse با لیست آیتم‌ها یا تعداد کل
	"""
	model = model_config['model']
	soft_delete_field = model_config.get('soft_delete_field', 'trashed')
	
	# ============================================================================
	# مرحله ۱: دریافت فیلدهای مجاز برای این کاربر
	# ============================================================================
	
	allowed_readable_fields = check_field_permissions(
		request.user,
		model,
		model_config['readable_fields'],
		'read'
	)
	
	# ============================================================================
	# مرحله ۲: پردازش پارامترهای query string
	# ============================================================================
	
	fields_str = request.GET.get('fields', 'all')
	page_number = int(request.GET.get('page', 1))
	page_size = min(int(request.GET.get('page_size', DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
	count_only = request.GET.get('count', '').lower() == 'true'
	ordering_str = request.GET.get('ordering', '')
	
	# پردازش فیلدهای درخواستی
	requested_fields = parse_fields_param(fields_str, allowed_readable_fields)
	
	# ============================================================================
	# مرحله ۳: ساخت QuerySet پایه
	# ============================================================================
	
	# فقط آیتم‌های حذف‌نشده را برمی‌گردانیم
	queryset = model.objects.filter(**{soft_delete_field: False})
	
	# فیلتر سطح object-level (اگر کاربر فقط به آیتم‌های خودش دسترسی دارد)
	# این منطق بسته به نیاز پروژه تغییر می‌کند
	# مثال: اگر مدل فیلد owner دارد
	if hasattr(model, 'owner') and not request.user.is_staff:
		queryset = queryset.filter(owner=request.user)
	
	# ============================================================================
	# مرحله ۴: اگر فقط تعداد خواسته شده، کوئری را بهینه می‌کنیم
	# ============================================================================
	
	if count_only:
		total_count = queryset.count()
		return api_response(
			success=True,
			data={'count': total_count}
		)
	
	# ============================================================================
	# مرحله ۵: اعمال مرتب‌سازی
	# ============================================================================
	
	ordering_fields = parse_ordering_param(ordering_str, allowed_readable_fields)
	queryset = queryset.order_by(*ordering_fields)
	
	# ============================================================================
	# مرحله ۶: بهینه‌سازی QuerySet (جلوگیری از N+1)
	# ============================================================================
	
	queryset = optimize_queryset_for_fields(queryset, requested_fields, model_config)
	
	# ============================================================================
	# مرحله ۷: اعمال Pagination
	# ============================================================================
	
	paginator = Paginator(queryset, page_size)
	total_count = paginator.count
	num_pages = paginator.num_pages
	
	try:
		page_obj = paginator.page(page_number)
	except EmptyPage:
		# اگر شماره صفحه نامعتبر بود، صفحه خالی برمی‌گردانیم
		return api_response(
			success=True,
			data={
				'count': total_count,
				'page': page_number,
				'page_size': page_size,
				'num_pages': num_pages,
				'next': None,
				'previous': None,
				'results': []
			}
		)
	
	# ============================================================================
	# مرحله ۸: سریالایز کردن آبجکت‌ها
	# ============================================================================
	
	results = [
		serialize_object(obj, requested_fields)
		for obj in page_obj.object_list
	]
	
	# ============================================================================
	# مرحله ۹: ساخت پاسخ نهایی
	# ============================================================================
	
	response_data = {
		'count': total_count,
		'page': page_number,
		'page_size': page_size,
		'num_pages': num_pages,
		'next': page_obj.next_page_number() if page_obj.has_next() else None,
		'previous': page_obj.previous_page_number() if page_obj.has_previous() else None,
		'results': results
	}
	
	return api_response(success=True, data=response_data)


def handle_create_request(request, model_name: str, model_config: Dict) -> JsonResponse:
	"""
	مدیریت درخواست POST برای ایجاد آیتم جدید
	
	این تابع موارد زیر را رعایت می‌کند:
	- فقط فیلدهای writable_fields را می‌پذیرد (محافظت در برابر mass assignment)
	- اعتبارسنجی کامل مدل
	- استفاده از تراکنش برای یکپارچگی داده
	- برگرداندن آبجکت ایجادشده
	
	Args:
		request: شیء HttpRequest
		model_name: نام مدل
		model_config: تنظیمات مدل از رجیستری
	
	Returns:
		JsonResponse با آبجکت ایجادشده
	"""
	model = model_config['model']
	
	# ============================================================================
	# مرحله ۱: دریافت فیلدهای قابل نوشتن برای این کاربر
	# ============================================================================
	
	allowed_writable_fields = check_field_permissions(
		request.user,
		model,
		model_config['writable_fields'],
		'write'
	)
	
	# ============================================================================
	# مرحله ۲: پردازش بدنه درخواست
	# ============================================================================
	
	try:
		body_data = json.loads(request.body.decode('utf-8'))
	except (json.JSONDecodeError, UnicodeDecodeError):
		return api_response(
			success=False,
			error={
				'code': ERROR_CODES['INVALID_INPUT'],
				'message': 'فرمت JSON بدنه درخواست نامعتبر است.',
				'details': {}
			},
			status=400
		)
	
	# ============================================================================
	# مرحله ۳: فیلتر کردن فیلدها (محافظت در برابر mass assignment)
	# ============================================================================
	
	# فقط فیلدهای مجاز را نگه می‌داریم، بقیه بی‌صدا حذف می‌شوند
	filtered_data = {
		key: value
		for key, value in body_data.items()
		if key in allowed_writable_fields
	}
	
	# اگر مدل فیلد owner دارد، آن را خودکار تنظیم می‌کنیم
	if hasattr(model, 'owner') and 'owner' in model_config['writable_fields']:
		filtered_data['owner'] = request.user
	
	# ============================================================================
	# مرحله ۴: ایجاد و ذخیره آبجکت در تراکنش
	# ============================================================================
	
	try:
		with transaction.atomic():
			# ایجاد نمونه جدید
			obj = model(**filtered_data)
			
			# اعتبارسنجی کامل (فراخوانی متد clean مدل)
			obj.full_clean()
			
			# ذخیره در دیتابیس
			obj.save()
		
		# ============================================================================
		# مرحله ۵: برگرداندن آبجکت ایجادشده
		# ============================================================================
		
		# فیلدهای قابل خواندن را دریافت می‌کنیم
		allowed_readable_fields = check_field_permissions(
			request.user,
			model,
			model_config['readable_fields'],
			'read'
		)
		
		# سریالایز کردن آبجکت ایجادشده
		serialized_obj = serialize_object(obj, list(allowed_readable_fields))
		
		return api_response(
			success=True,
			data={'data': serialized_obj},
			status=201
		)
	
	except Exception as e:
		# ============================================================================
		# مرحله ۶: مدیریت خطاهای اعتبارسنجی
		# ============================================================================
		
		# اگر ValidationError باشد، جزئیات را برمی‌گردانیم
		if hasattr(e, 'message_dict'):
			error_details = e.message_dict
		elif hasattr(e, 'messages'):
			error_details = {'non_field_errors': e.messages}
		else:
			error_details = {'error': str(e)}
		
		return api_response(
			success=False,
			error={
				'code': ERROR_CODES['VALIDATION_ERROR'],
				'message': 'اعتبارسنجی شکست خورد.',
				'details': error_details
			},
			status=400
		)

def model_help(request, model_name):
	"""
	نمایش راهنمای کامل API برای یک مدل خاص
	
	این endpoint اطلاعات کاملی درباره قابلیت‌های مجاز کاربر
	روی مدل مورد نظر ارائه می‌دهد، شامل:
	- فیلدهای قابل خواندن/نوشتن
	- متدهای قابل فراخوانی
	- پارامترهای مجاز
	- نمونه‌های درخواست و پاسخ
	
	Args:
		request: HttpRequest object
		model_name: نام مدل (مثل 'customer')
	
	Returns:
		JsonResponse حاوی راهنمای کامل
	
	مثال درخواست:
		GET /api/customer/help/
	
	مثال پاسخ:
		{
			"status": "success",
			"data": {
				"model": "customer",
				"verbose_name": "مشتری",
				"verbose_name_plural": "مشتریان",
				"lookup_field": "id",
				"permissions": {
					"can_list": true,
					"can_retrieve": true,
					"can_create": true,
					"can_update": true,
					"can_delete": false,
					"can_permanent_delete": false
				},
				"fields": {
					"readable": [...],
					"writable": [...]
				},
				"methods": [...],
				"endpoints": [...],
				"rate_limits": {...},
				"examples": {...}
			}
		}
	"""
	try:
		# بررسی احراز هویت
		if not request.user.is_authenticated:
			return JsonResponse({
				'status': 'error',
				'message': 'برای دسترسی به این بخش باید وارد شوید.',
				'error_code': 'AUTHENTICATION_REQUIRED'
			}, status=401)
		
		# دریافت اطلاعات مدل از رجیستری
		model_config = model_registry.get(model_name.lower())
		if not model_config:
			return JsonResponse({
				'status': 'error',
				'message': f'مدل "{model_name}" در سیستم ثبت نشده است.',
				'error_code': 'MODEL_NOT_FOUND',
				'available_models': list(MODEL_REGISTRY.keys())
			}, status=404)
		
		model_class = model_config['model']
		
		# بررسی دسترسی به مدل
		if not has_model_permission(request.user, model_class, 'view'):
			return JsonResponse({
				'status': 'error',
				'message': 'شما مجوز دسترسی به این مدل را ندارید.',
				'error_code': 'MODEL_ACCESS_DENIED'
			}, status=403)
		
		# ساخت اطلاعات پایه مدل
		model_info = {
			'model': model_name,
			'verbose_name': model_class._meta.verbose_name,
			'verbose_name_plural': model_class._meta.verbose_name_plural,
			'lookup_field': model_config.get('lookup_field', 'pk'),
			'description': model_class.__doc__ or f'مدل {model_class._meta.verbose_name}',
		}
		
		# بررسی مجوزهای سطح مدل
		permissions = {
			'can_list': has_model_permission(request.user, model_class, 'view'),
			'can_retrieve': has_model_permission(request.user, model_class, 'view'),
			'can_create': has_model_permission(request.user, model_class, 'add'),
			'can_update': has_model_permission(request.user, model_class, 'change'),
			'can_delete': has_model_permission(request.user, model_class, 'delete'),
			'can_permanent_delete': (
				has_model_permission(request.user, model_class, 'delete') and
				request.user.has_perm(f'{model_class._meta.app_label}.permanent_delete_{model_class._meta.model_name}')
			)
		}
		
		# استخراج فیلدهای قابل خواندن
		readable_fields = _get_readable_fields_info(
			request.user,
			model_class,
			model_config
		)
		
		# استخراج فیلدهای قابل نوشتن
		writable_fields = _get_writable_fields_info(
			request.user,
			model_class,
			model_config
		)
		
		# استخراج متدهای قابل فراخوانی
		callable_methods = _get_callable_methods_info(
			request.user,
			model_class,
			model_config
		)
		
		# ساخت لیست endpoint‌ها
		base_url = f'/api/{model_name}'
		lookup_field = model_config.get('lookup_field', 'pk')
		
		endpoints = [
			{
				'method': 'GET',
				'path': f'{base_url}/',
				'description': 'دریافت لیست آیتم‌ها',
				'requires_auth': True,
				'parameters': [
					{'name': 'fields', 'type': 'string', 'description': 'فیلدهای مورد نظر جدا شده با کاما'},
					{'name': 'page', 'type': 'integer', 'description': 'شماره صفحه (پیش‌فرض: 1)'},
					{'name': 'page_size', 'type': 'integer', 'description': 'تعداد آیتم در صفحه (پیش‌فرض: 20، حداکثر: 100)'},
					{'name': 'count', 'type': 'boolean', 'description': 'نمایش تعداد کل (پیش‌فرض: true)'},
					{'name': 'ordering', 'type': 'string', 'description': 'مرتب‌سازی (مثال: -created_at)'},
				]
			} if permissions['can_list'] else None,
			
			{
				'method': 'POST',
				'path': f'{base_url}/',
				'description': 'ایجاد آیتم جدید',
				'requires_auth': True,
				'body_example': {field['name']: field.get('example', '...') for field in writable_fields[:3]}
			} if permissions['can_create'] else None,
			
			{
				'method': 'GET',
				'path': f'{base_url}/<{lookup_field}>/',
				'description': 'دریافت جزئیات یک آیتم',
				'requires_auth': True,
				'parameters': [
					{'name': 'fields', 'type': 'string', 'description': 'فیلدهای مورد نظر جدا شده با کاما'},
				]
			} if permissions['can_retrieve'] else None,
			
			{
				'method': 'PUT',
				'path': f'{base_url}/<{lookup_field}>/',
				'description': 'بروزرسانی کامل یک آیتم',
				'requires_auth': True,
			} if permissions['can_update'] else None,
			
			{
				'method': 'PATCH',
				'path': f'{base_url}/<{lookup_field}>/',
				'description': 'بروزرسانی جزئی یک آیتم',
				'requires_auth': True,
			} if permissions['can_update'] else None,
			
			{
				'method': 'DELETE',
				'path': f'{base_url}/<{lookup_field}>/',
				'description': 'حذف نرم (soft delete)',
				'requires_auth': True,
			} if permissions['can_delete'] else None,
			
			{
				'method': 'DELETE',
				'path': f'{base_url}/<{lookup_field}>/?permanent=true',
				'description': 'حذف دائمی از دیتابیس',
				'requires_auth': True,
				'warning': 'این عملیات غیرقابل بازگشت است'
			} if permissions['can_permanent_delete'] else None,
		]
		
		# اضافه کردن endpoint‌های متد
		for method in callable_methods:
			endpoints.append({
				'method': 'POST',
				'path': f'{base_url}/<{lookup_field}>/call/{method["name"]}/',
				'description': method.get('description', f'فراخوانی متد {method["name"]}'),
				'requires_auth': True,
				'parameters': method.get('parameters', []),
				'rate_limit': method.get('rate_limit'),
			})
		
		# حذف endpoint‌های None (مجوزهای نداشته)
		endpoints = [ep for ep in endpoints if ep is not None]
		
		# اطلاعات محدودیت نرخ
		rate_limits = {
			'default': {
				'max_requests': DEFAULT_RATE_LIMIT_MAX_REQUESTS,
				'window_seconds': DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
				'description': f'{DEFAULT_RATE_LIMIT_MAX_REQUESTS} درخواست در هر {DEFAULT_RATE_LIMIT_WINDOW_SECONDS} ثانیه'
			}
		}
		
		# اضافه کردن محدودیت‌های خاص متدها
		for method in callable_methods:
			if method.get('rate_limit'):
				rate_limits[f'method_{method["name"]}'] = method['rate_limit']
		
		# ساخت نمونه‌های درخواست/پاسخ
		examples = _generate_examples(
			model_name,
			model_class,
			readable_fields,
			writable_fields,
			callable_methods,
			lookup_field
		)
		
		# ساخت پاسخ نهایی
		help_data = {
			**model_info,
			'permissions': permissions,
			'fields': {
				'readable': readable_fields,
				'writable': writable_fields,
				'total_readable': len(readable_fields),
				'total_writable': len(writable_fields),
			},
			'methods': callable_methods,
			'endpoints': endpoints,
			'rate_limits': rate_limits,
			'examples': examples,
			'notes': [
				'برای دریافت فیلدهای خاص از پارامتر fields استفاده کنید',
				'حداکثر تعداد فیلدها در هر درخواست: 50',
				'حداکثر page_size: 100',
				'برای حذف دائمی به مجوز ویژه نیاز است',
				'تمام متدها نیاز به احراز هویت دارند',
			]
		}
		
		return JsonResponse({
			'status': 'success',
			'data': help_data
		}, status=200)
		
	except Exception as e:
		logger.exception(f'خطا در help endpoint برای مدل {model_name}')
		
		return JsonResponse({
			'status': 'error',
			'message': 'خطایی در سرور رخ داده است.',
			'error_code': 'INTERNAL_SERVER_ERROR'
		}, status=500)


# ============================================================================
# توابع کمکی برای استخراج اطلاعات
# ============================================================================

def _get_readable_fields_info(user, model_class, model_config):
	"""
	استخراج اطلاعات کامل فیلدهای قابل خواندن برای کاربر
	
	Returns:
		لیستی از دیکشنری‌ها حاوی اطلاعات هر فیلد
	"""
	readable_fields = model_config.get('readable_fields', '__all__')
	
	# اگر __all__ است، تمام فیلدها را برمی‌گردانیم
	if readable_fields == '__all__':
		all_fields = [f.name for f in model_class._meta.get_fields() 
					  if not f.many_to_many and not f.one_to_many]
	else:
		all_fields = readable_fields
	
	fields_info = []
	
	for field_name in all_fields:
		# بررسی دسترسی به فیلد
		if not has_field_permission(user, model_class, field_name, 'read'):
			continue
		
		try:
			field = model_class._meta.get_field(field_name)
			
			field_info = {
				'name': field_name,
				'verbose_name': getattr(field, 'verbose_name', field_name),
				'type': field.get_internal_type(),
				'required': not field.blank if hasattr(field, 'blank') else False,
				'null': field.null if hasattr(field, 'null') else False,
				'help_text': field.help_text if hasattr(field, 'help_text') else '',
			}
			
			# اضافه کردن اطلاعات خاص به نوع فیلد
			if hasattr(field, 'max_length') and field.max_length:
				field_info['max_length'] = field.max_length
			
			if hasattr(field, 'choices') and field.choices:
				field_info['choices'] = [
					{'value': choice[0], 'label': choice[1]}
					for choice in field.choices
				]
			
			if field.get_internal_type() in ['ForeignKey', 'OneToOneField']:
				field_info['related_model'] = field.related_model.__name__.lower()
				field_info['related_field'] = field.remote_field.field_name
			
			if hasattr(field, 'default') and field.default is not models.NOT_PROVIDED:
				try:
					default_value = field.default() if callable(field.default) else field.default
					field_info['default'] = str(default_value)
				except:
					field_info['default'] = None
			
			fields_info.append(field_info)
			
		except Exception:
			# فیلد پیدا نشد یا خطا در استخراج اطلاعات
			continue
	
	return fields_info


def _get_writable_fields_info(user, model_class, model_config):
	"""
	استخراج اطلاعات کامل فیلدهای قابل نوشتن برای کاربر
	"""
	writable_fields = model_config.get('writable_fields', [])
	
	fields_info = []
	
	for field_name in writable_fields:
		# بررسی دسترسی به فیلد
		if not has_field_permission(user, model_class, field_name, 'write'):
			continue
		
		try:
			field = model_class._meta.get_field(field_name)
			
			field_info = {
				'name': field_name,
				'verbose_name': getattr(field, 'verbose_name', field_name),
				'type': field.get_internal_type(),
				'required': not field.blank if hasattr(field, 'blank') else False,
				'null': field.null if hasattr(field, 'null') else False,
				'help_text': field.help_text if hasattr(field, 'help_text') else '',
			}
			
			# اطلاعات اعتبارسنجی
			validators = []
			if hasattr(field, 'max_length') and field.max_length:
				field_info['max_length'] = field.max_length
				validators.append(f'حداکثر {field.max_length} کاراکتر')
			
			if hasattr(field, 'min_length') and field.min_length:
				field_info['min_length'] = field.min_length
				validators.append(f'حداقل {field.min_length} کاراکتر')
			
			if hasattr(field, 'max_value') and field.max_value:
				field_info['max_value'] = field.max_value
				validators.append(f'حداکثر مقدار: {field.max_value}')
			
			if hasattr(field, 'min_value') and field.min_value:
				field_info['min_value'] = field.min_value
				validators.append(f'حداقل مقدار: {field.min_value}')
			
			if validators:
				field_info['validators'] = validators
			
			# گزینه‌ها
			if hasattr(field, 'choices') and field.choices:
				field_info['choices'] = [
					{'value': choice[0], 'label': choice[1]}
					for choice in field.choices
				]
			
			# مثال مقدار
			field_info['example'] = _generate_field_example(field)
			
			fields_info.append(field_info)
			
		except Exception:
			continue
	
	return fields_info


def _get_callable_methods_info(user, model_class, model_config):
	"""
	استخراج اطلاعات متدهای قابل فراخوانی
	"""
	callable_methods = model_config.get('callable_methods', [])
	
	methods_info = []
	
	for method_name in callable_methods:
		# بررسی دسترسی به متد
		if not has_method_permission(user, model_class, method_name):
			continue
		
		try:
			method = getattr(model_class, method_name)
			
			method_info = {
				'name': method_name,
				'description': method.__doc__ or f'فراخوانی متد {method_name}',
			}
			
			# استخراج پارامترها از signature
			
			sig = inspect.signature(method)
			parameters = []
			
			for param_name, param in sig.parameters.items():
				if param_name in ['self', 'request']:
					continue
				
				param_info = {
					'name': param_name,
					'required': param.default == inspect.Parameter.empty,
				}
				
				if param.annotation != inspect.Parameter.empty:
					param_info['type'] = param.annotation.__name__
				
				if param.default != inspect.Parameter.empty:
					param_info['default'] = str(param.default)
				
				parameters.append(param_info)
			
			if parameters:
				method_info['parameters'] = parameters
			
			# اگر متد rate limit دارد
			if hasattr(method, '_rate_limit'):
				method_info['rate_limit'] = method._rate_limit
			
			methods_info.append(method_info)
			
		except Exception:
			continue
	
	return methods_info


def _generate_field_example(field):
	"""
	تولید مقدار نمونه برای فیلد بر اساس نوع آن
	"""
	field_type = field.get_internal_type()
	
	examples = {
		'CharField': 'نمونه متن',
		'TextField': 'متن طولانی نمونه',
		'IntegerField': 123,
		'FloatField': 123.45,
		'DecimalField': '123.45',
		'BooleanField': True,
		'DateField': '2026-06-27',
		'DateTimeField': '2026-06-27T10:30:00Z',
		'EmailField': 'example@domain.com',
		'URLField': 'https://example.com',
		'PhoneNumberField': '09123456789',
		'ForeignKey': 1,
		'OneToOneField': 1,
	}
	
	return examples.get(field_type, 'مقدار نمونه')


def _generate_examples(model_name, model_class, readable_fields, writable_fields, callable_methods, lookup_field):
	"""
	تولید نمونه‌های درخواست و پاسخ
	"""
	examples = {}
	
	# مثال لیست
	if readable_fields:
		sample_fields = readable_fields[:3]
		examples['list'] = {
			'request': f'GET /api/{model_name}/?fields={",".join([f["name"] for f in sample_fields])}&page=1&page_size=20',
			'response': {
				'status': 'success',
				'data': {
					'items': [
						{field['name']: field.get('example', '...') for field in sample_fields}
					],
					'pagination': {
						'current_page': 1,
						'page_size': 20,
						'total_items': 100,
						'total_pages': 5
					}
				}
			}
		}
	
	# مثال ایجاد
	if writable_fields:
		sample_data = {field['name']: field.get('example', '...') for field in writable_fields[:3]}
		examples['create'] = {
			'request': f'POST /api/{model_name}/',
			'body': sample_data,
			'response': {
				'status': 'success',
				'data': {
					lookup_field: 1,
					**sample_data
				}
			}
		}
	
	# مثال فراخوانی متد
	if callable_methods:
		method = callable_methods[0]
		examples['call_method'] = {
			'request': f'POST /api/{model_name}/1/call/{method["name"]}/',
			'body': {p['name']: '...' for p in method.get('parameters', [])},
			'response': {
				'status': 'success',
				'data': {
					'result': '...'
				}
			}
		}
	
	return examples


def call_model_method(request, model_name, lookup_value, method_name):
	"""
	فراخوانی یک متد روی یک instance از مدل
	
	این endpoint امکان اجرای متدهای whitelist شده روی یک شی خاص از مدل را
	فراهم می‌کند. تنها متدهای ثبت شده در رجیستری قابل فراخوانی هستند.
	
	ویژگی‌های امنیتی:
	- فقط متدهای whitelist شده قابل اجرا هستند
	- بررسی مجوز در چهار سطح: مدل، شی، متد و پارامترها
	- محدودیت نرخ (rate limiting) برای متدهای پرهزینه
	- اعتبارسنجی ورودی و جلوگیری از تزریق کد
	- لاگ تمام فراخوانی‌ها برای audit trail
	
	Args:
		request: HttpRequest object (باید POST باشد)
		model_name: نام مدل (مثل 'customer')
		lookup_value: مقدار lookup field (مثل ID)
		method_name: نام متد برای فراخوانی
	
	Returns:
		JsonResponse حاوی نتیجه متد یا خطا
	
	مثال درخواست:
		POST /api/customer/123/call/activate/
		{
			"reason": "تایید هویت موفق",
			"notify_user": true
		}
	
	مثال پاسخ موفق:
		{
			"status": "success",
			"data": {
				"result": {
					"activated": true,
					"activation_date": "2026-06-27T10:30:00Z"
				},
				"message": "متد با موفقیت اجرا شد"
			}
		}
	
	خطاهای محتمل:
		401: عدم احراز هویت
		403: عدم مجوز دسترسی
		404: مدل، شی یا متد پیدا نشد
		405: استفاده از HTTP method غیرمجاز
		429: عبور از محدودیت نرخ
		400: پارامترهای نامعتبر
		500: خطای داخلی سرور
	"""
	# ===================================================================
	# بررسی HTTP method
	# ===================================================================
	if request.method != 'POST':
		return JsonResponse({
			'status': 'error',
			'message': 'فقط متد POST مجاز است.',
			'error_code': 'METHOD_NOT_ALLOWED'
		}, status=405)
	
	try:
		# ===============================================================
		# احراز هویت
		# ===============================================================
		if not request.user.is_authenticated:
			return JsonResponse({
				'status': 'error',
				'message': 'برای فراخوانی متد باید وارد شوید.',
				'error_code': 'AUTHENTICATION_REQUIRED'
			}, status=401)
		
		# ===============================================================
		# بررسی وجود مدل در رجیستری
		# ===============================================================
		model_config = MODEL_REGISTRY.get(model_name)
		if not model_config:
			logger.warning(
				f'تلاش برای دسترسی به مدل ناموجود: {model_name} توسط کاربر {request.user.id}'
			)
			return JsonResponse({
				'status': 'error',
				'message': f'مدل "{model_name}" در سیستم ثبت نشده است.',
				'error_code': 'MODEL_NOT_FOUND',
				'available_models': list(MODEL_REGISTRY.keys())
			}, status=404)
		
		model_class = model_config['model']
		
		# ===============================================================
		# بررسی مجوز سطح مدل
		# ===============================================================
		if not has_model_permission(request.user, model_class, 'view'):
			logger.warning(
				f'کاربر {request.user.id} مجوز دسترسی به مدل {model_name} را ندارد'
			)
			return JsonResponse({
				'status': 'error',
				'message': 'شما مجوز دسترسی به این مدل را ندارید.',
				'error_code': 'MODEL_ACCESS_DENIED'
			}, status=403)
		
		# ===============================================================
		# بررسی whitelist متدها
		# ===============================================================
		callable_methods = model_config.get('callable_methods', [])
		if method_name not in callable_methods:
			logger.warning(
				f'تلاش برای فراخوانی متد غیرمجاز {method_name} روی {model_name} توسط کاربر {request.user.id}'
			)
			return JsonResponse({
				'status': 'error',
				'message': f'متد "{method_name}" برای فراخوانی مجاز نیست.',
				'error_code': 'METHOD_NOT_ALLOWED',
				'allowed_methods': callable_methods
			}, status=403)
		
		# ===============================================================
		# بررسی مجوز سطح متد
		# ===============================================================
		if not has_method_permission(request.user, model_class, method_name):
			logger.warning(
				f'کاربر {request.user.id} مجوز فراخوانی متد {method_name} را ندارد'
			)
			return JsonResponse({
				'status': 'error',
				'message': f'شما مجوز فراخوانی متد "{method_name}" را ندارید.',
				'error_code': 'METHOD_PERMISSION_DENIED'
			}, status=403)
		
		# ===============================================================
		# دریافت شی از دیتابیس
		# ===============================================================
		lookup_field = model_config.get('lookup_field', 'pk')
		
		try:
			obj = model_class.objects.get(**{lookup_field: lookup_value})
		except model_class.DoesNotExist:
			return JsonResponse({
				'status': 'error',
				'message': f'آیتم با {lookup_field}={lookup_value} پیدا نشد.',
				'error_code': 'OBJECT_NOT_FOUND'
			}, status=404)
		except model_class.MultipleObjectsReturned:
			logger.error(
				f'چند شی با {lookup_field}={lookup_value} در {model_name} پیدا شد'
			)
			return JsonResponse({
				'status': 'error',
				'message': 'خطای داخلی: چند آیتم با این شناسه پیدا شد.',
				'error_code': 'MULTIPLE_OBJECTS_FOUND'
			}, status=500)
		except ValueError:
			return JsonResponse({
				'status': 'error',
				'message': f'مقدار {lookup_field} نامعتبر است.',
				'error_code': 'INVALID_LOOKUP_VALUE'
			}, status=400)
		
		# ===============================================================
		# بررسی مجوز سطح شی
		# ===============================================================
		if not check_object_permission(request.user, obj, 'view'):
			logger.warning(
				f'کاربر {request.user.id} مجوز دسترسی به شی {obj.pk} از {model_name} را ندارد'
			)
			return JsonResponse({
				'status': 'error',
				'message': 'شما مجوز دسترسی به این آیتم را ندارید.',
				'error_code': 'OBJECT_ACCESS_DENIED'
			}, status=403)
		
		# ===============================================================
		# بررسی محدودیت نرخ (Rate Limiting)
		# ===============================================================
		method = getattr(model_class, method_name)
		
		# بررسی rate limit خاص متد
		if hasattr(method, '_rate_limit'):
			rate_limit_config = method._rate_limit
			rate_limit_key = f'method_call:{request.user.id}:{model_name}:{method_name}'
			
			if not check_rate_limit(
				rate_limit_key,
				max_requests=rate_limit_config.get('max_requests', DEFAULT_RATE_LIMIT_MAX_REQUESTS),
				window_seconds=rate_limit_config.get('window_seconds', DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
			):
				logger.warning(
					f'کاربر {request.user.id} از محدودیت نرخ متد {method_name} عبور کرد'
				)
				return JsonResponse({
					'status': 'error',
					'message': 'شما از حد مجاز تعداد درخواست عبور کرده‌اید. لطفا بعدا تلاش کنید.',
					'error_code': 'RATE_LIMIT_EXCEEDED',
					'retry_after': rate_limit_config.get('window_seconds', DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
				}, status=429)
		
		# rate limit پیش‌فرض برای متدهایی که rate limit خاص ندارند
		else:
			default_rate_limit_key = f'method_call:{request.user.id}:{model_name}'
			if not check_rate_limit(
				default_rate_limit_key,
				max_requests=DEFAULT_RATE_LIMIT_MAX_REQUESTS,
				window_seconds=DEFAULT_RATE_LIMIT_WINDOW_SECONDS
			):
				logger.warning(
					f'کاربر {request.user.id} از محدودیت نرخ پیش‌فرض متدها عبور کرد'
				)
				return JsonResponse({
					'status': 'error',
					'message': 'شما از حد مجاز تعداد درخواست عبور کرده‌اید.',
					'error_code': 'RATE_LIMIT_EXCEEDED',
					'retry_after': DEFAULT_RATE_LIMIT_WINDOW_SECONDS
				}, status=429)
		
		# ===============================================================
		# پارس کردن پارامترهای ورودی
		# ===============================================================
		try:
			if request.content_type == 'application/json':
				method_params = json.loads(request.body) if request.body else {}
			else:
				method_params = dict(request.POST)
		except json.JSONDecodeError:
			return JsonResponse({
				'status': 'error',
				'message': 'فرمت JSON نامعتبر است.',
				'error_code': 'INVALID_JSON'
			}, status=400)
		
		# ===============================================================
		# اعتبارسنجی پارامترها
		# ===============================================================
		validation_result = _validate_method_parameters(
			method,
			method_params,
			request.user,
			obj
		)
		
		if not validation_result['valid']:
			return JsonResponse({
				'status': 'error',
				'message': validation_result['message'],
				'error_code': 'INVALID_PARAMETERS',
				'details': validation_result.get('details', {})
			}, status=400)
		
		validated_params = validation_result['params']
		
		# ===============================================================
		# فراخوانی متد
		# ===============================================================
		try:
			# بررسی signature متد برای تشخیص نیاز به request
			sig = inspect.signature(method)
			
			# اگر متد پارامتر request می‌خواهد، آن را اضافه می‌کنیم
			if 'request' in sig.parameters:
				result = method(request=request, **validated_params)
			else:
				result = method(**validated_params)
			
			# ===============================================================
			# لاگ موفقیت
			# ===============================================================
			logger.info(
				f'کاربر {request.user.id} متد {method_name} را روی {model_name}:{obj.pk} فراخوانی کرد'
			)
			
			# ===============================================================
			# serialize کردن نتیجه
			# ===============================================================
			serialized_result = _serialize_method_result(result, model_class)
			
			return JsonResponse({
				'status': 'success',
				'data': {
					'result': serialized_result,
					'message': 'متد با موفقیت اجرا شد.',
					'executed_at': timezone.now().isoformat()
				}
			}, status=200)
			
		except PermissionDenied as e:
			# متد خودش مجوز را رد کرد
			logger.warning(
				f'متد {method_name} مجوز کاربر {request.user.id} را رد کرد: {str(e)}'
			)
			return JsonResponse({
				'status': 'error',
				'message': str(e) or 'شما مجوز اجرای این عملیات را ندارید.',
				'error_code': 'PERMISSION_DENIED'
			}, status=403)
			
		except ValidationError as e:
			# خطای اعتبارسنجی داخل متد
			logger.info(
				f'خطای اعتبارسنجی در متد {method_name}: {str(e)}'
			)
			return JsonResponse({
				'status': 'error',
				'message': 'داده‌های ورودی نامعتبر است.',
				'error_code': 'VALIDATION_ERROR',
				'details': e.message_dict if hasattr(e, 'message_dict') else {'error': str(e)}
			}, status=400)
			
		except ObjectDoesNotExist as e:
			# شی مورد نیاز در متد پیدا نشد
			logger.warning(
				f'شی مورد نیاز در متد {method_name} پیدا نشد: {str(e)}'
			)
			return JsonResponse({
				'status': 'error',
				'message': 'یکی از آیتم‌های مورد نیاز پیدا نشد.',
				'error_code': 'RELATED_OBJECT_NOT_FOUND',
				'details': str(e)
			}, status=404)
			
		except Exception as e:
			# error on متد
			logger.exception(
				f'خطا در اجرای متد {method_name} روی {model_name}:{obj.pk}'
			)
			return JsonResponse({
				'status': 'error',
				'message': f'خطا در اجرای متد: {str(e)}',
				'error_code': 'METHOD_EXECUTION_ERROR'
			}, status=500)
	
	except Exception as e:
		# خطای کلی
		logger.exception(
			f'error on call_model_method برای {model_name}/{lookup_value}/{method_name}'
		)
		return JsonResponse({
			'status': 'error',
			'message': 'خطایی در سرور رخ داده است.',
			'error_code': 'INTERNAL_SERVER_ERROR'
		}, status=500)


# ============================================================================
# توابع کمکی
# ============================================================================

def _validate_method_parameters(method, params, user, obj):
	"""
	اعتبارسنجی پارامترهای ورودی متد
	
	این تابع:
	- بررسی می‌کند تمام پارامترهای required موجود باشند
	- نوع پارامترها را بررسی می‌کند (در صورت امکان)
	- پارامترهای اضافی را حذف می‌کند
	- مقادیر را به نوع صحیح تبدیل می‌کند
	
	Args:
		method: متد برای اجرا
		params: دیکشنری پارامترهای ورودی
		user: کاربر جاری
		obj: شی مدل
	
	Returns:
		دیکشنری حاوی:
		- valid: bool
		- params: پارامترهای اعتبارسنجی شده
		- message: پیام خطا (در صورت invalid بودن)
		- details: جزئیات خطا
	"""
	
	
	try:
		sig = inspect.signature(method)
		validated_params = {}
		errors = {}
		
		for param_name, param in sig.parameters.items():
			# skip کردن self و request
			if param_name in ['self', 'request']:
				continue
			
			# بررسی وجود پارامتر required
			if param.default == inspect.Parameter.empty:
				if param_name not in params:
					errors[param_name] = f'پارامتر "{param_name}" الزامی است.'
					continue
			
			# دریافت مقدار
			value = params.get(param_name, param.default)
			
			# اگر مقدار None است و پارامتر optional است
			if value is None and param.default != inspect.Parameter.empty:
				validated_params[param_name] = None
				continue
			
			# تبدیل نوع بر اساس annotation
			if param.annotation != inspect.Parameter.empty:
				try:
					annotation = param.annotation
					
					# انواع ساده
					if annotation == int:
						validated_params[param_name] = int(value)
					elif annotation == float:
						validated_params[param_name] = float(value)
					elif annotation == str:
						validated_params[param_name] = str(value)
					elif annotation == bool:
						# مدیریت boolean از string
						if isinstance(value, str):
							validated_params[param_name] = value.lower() in ['true', '1', 'yes']
						else:
							validated_params[param_name] = bool(value)
					
					# انواع پیشرفته‌تر
					elif annotation == Decimal:
						validated_params[param_name] = Decimal(str(value))
					elif annotation == datetime:
						if isinstance(value, str):
							validated_params[param_name] = datetime.fromisoformat(value.replace('Z', '+00:00'))
						else:
							validated_params[param_name] = value
					elif annotation == date:
						if isinstance(value, str):
							validated_params[param_name] = datetime.fromisoformat(value).date()
						else:
							validated_params[param_name] = value
					
					# لیست و دیکشنری
					elif hasattr(annotation, '__origin__'):
						if annotation.__origin__ == list:
							if isinstance(value, str):
								validated_params[param_name] = json.loads(value)
							else:
								validated_params[param_name] = list(value)
						elif annotation.__origin__ == dict:
							if isinstance(value, str):
								validated_params[param_name] = json.loads(value)
							else:
								validated_params[param_name] = dict(value)
						else:
							validated_params[param_name] = value
					else:
						validated_params[param_name] = value
				
				except (ValueError, TypeError, InvalidOperation, json.JSONDecodeError) as e:
					type_name = annotation.__name__ if hasattr(annotation, '__name__') else str(annotation)
					errors[param_name] = f'مقدار "{value}" برای نوع {type_name} نامعتبر است.'
					continue
			else:
				# بدون annotation، مقدار را همان‌طور قبول می‌کنیم
				validated_params[param_name] = value
		
		# بررسی پارامترهای اضافی
		extra_params = set(params.keys()) - set(sig.parameters.keys())
		if extra_params:
			# فقط هشدار می‌دهیم، خطا نمی‌دهیم
			pass
		
		if errors:
			return {
				'valid': False,
				'message': 'پارامترهای ورودی نامعتبر هستند.',
				'details': errors
			}
		
		return {
			'valid': True,
			'params': validated_params
		}
	
	except Exception as e:
		return {
			'valid': False,
			'message': f'خطا در اعتبارسنجی پارامترها: {str(e)}'
		}


def _serialize_method_result(result, model_class):
	"""
	تبدیل نتیجه متد به فرمت قابل serialize
	
	این تابع انواع مختلف نتایج را مدیریت می‌کند:
	- None, bool, int, float, str: مستقیم برمی‌گرداند
	- datetime, date: به ISO format تبدیل می‌کند
	- Decimal: به string تبدیل می‌کند
	- QuerySet: به لیست دیکشنری تبدیل می‌کند
	- Model instance: به دیکشنری تبدیل می‌کند
	- dict, list: بازگشتی serialize می‌کند
	
	Args:
		result: نتیجه متد
		model_class: کلاس مدل (برای context)
	
	Returns:
		نتیجه serialize شده
	"""
	
	
	# None و انواع ساده
	if result is None or isinstance(result, (bool, int, float, str)):
		return result
	
	# تاریخ و زمان
	if isinstance(result, datetime):
		return result.isoformat()
	if isinstance(result, date):
		return result.isoformat()
	
	# Decimal
	if isinstance(result, Decimal):
		return str(result)
	
	# فایل
	if isinstance(result, FieldFile):
		return result.url if result else None
	
	# QuerySet
	if isinstance(result, QuerySet):
		return [_serialize_model_instance(item) for item in result[:100]]  # محدود به 100 آیتم
	
	# Model instance
	if isinstance(result, Model):
		return _serialize_model_instance(result)
	
	# Dictionary
	if isinstance(result, dict):
		return {
			key: _serialize_method_result(value, model_class)
			for key, value in result.items()
		}
	
	# List/Tuple
	if isinstance(result, (list, tuple)):
		return [_serialize_method_result(item, model_class) for item in result]
	
	# سایر انواع: تبدیل به string
	try:
		return str(result)
	except:
		return None


def _serialize_model_instance(instance):
	"""
	تبدیل یک instance از Model به دیکشنری
	
	فقط فیلدهای ساده را برمی‌گرداند (بدون relation)
	"""
	
	
	data = {}
	
	for field in instance._meta.fields:
		field_name = field.name
		value = getattr(instance, field_name, None)
		
		if value is None:
			data[field_name] = None
		elif isinstance(value, (bool, int, float, str)):
			data[field_name] = value
		elif isinstance(value, (datetime, date)):
			data[field_name] = value.isoformat()
		elif isinstance(value, Decimal):
			data[field_name] = str(value)
		else:
			try:
				data[field_name] = str(value)
			except:
				data[field_name] = None
	
	return data

def object_help(request, model_name, lookup_value):
	"""
	نمایش اطلاعات جامع API یک شی (Instance) خاص از مدل
	
	این endpoint اطلاعات مفصل درباره یک شی خاص از مدل را به کاربر نمایش می‌دهد:
	- اطلاعات هویتی شی
	- فیلدهای قابل مشاهده و ویرایش
	- عملیات مجاز روی این شی
	- روابط و وابستگی‌ها
	- نمونه درخواست‌ها و پاسخ‌ها
	
	این view به کاربر کمک می‌کند تا بفهمد چه کارهایی می‌تواند روی یک شی خاص انجام دهد.
	
	Args:
		request: HttpRequest object
		model_name: نام مدل (مثل 'customer')
		lookup_value: مقدار lookup field (مثل ID)
	
	Returns:
		JsonResponse حاوی اطلاعات شی و عملیات مجاز
	
	مثال درخواست:
		GET /api/customer/123/object_help/
	
	مثال پاسخ:
		{
			"status": "success",
			"data": {
				"object_info": {...},
				"readable_fields": [...],
				"writable_fields": [...],
				"allowed_actions": ["update", "delete"],
				"relationships": {...},
				"examples": {...}
			}
		}
	"""
	try:
		# ===============================================================
		# احراز هویت
		# ===============================================================
		if not request.user.is_authenticated:
			return JsonResponse({
				'status': 'error',
				'message': 'برای دسترسی به اطلاعات شی باید وارد شوید.',
				'error_code': 'AUTHENTICATION_REQUIRED'
			}, status=401)
		
		# ===============================================================
		# بررسی وجود مدل در رجیستری
		# ===============================================================
		model_config = MODEL_REGISTRY.get(model_name)
		if not model_config:
			logger.warning(
				f'تلاش برای دسترسی به اطلاعات شی از مدل ناموجود: {model_name} توسط کاربر {request.user.id}'
			)
			return JsonResponse({
				'status': 'error',
				'message': f'مدل "{model_name}" در سیستم ثبت نشده است.',
				'error_code': 'MODEL_NOT_FOUND',
				'available_models': list(MODEL_REGISTRY.keys())
			}, status=404)
		
		model_class = model_config['model']
		lookup_field = model_config.get('lookup_field', 'pk')
		
		# ===============================================================
		# بررسی مجوز سطح مدل
		# ===============================================================
		if not has_model_permission(request.user, model_class, 'view'):
			return JsonResponse({
				'status': 'error',
				'message': 'شما مجوز دسترسی به این مدل را ندارید.',
				'error_code': 'MODEL_ACCESS_DENIED'
			}, status=403)
		
		# ===============================================================
		# دریافت شی از دیتابیس
		# ===============================================================
		try:
			obj = model_class.objects.get(**{lookup_field: lookup_value})
		except model_class.DoesNotExist:
			return JsonResponse({
				'status': 'error',
				'message': f'آیتم با {lookup_field}={lookup_value} پیدا نشد.',
				'error_code': 'OBJECT_NOT_FOUND'
			}, status=404)
		except model_class.MultipleObjectsReturned:
			logger.error(f'چند شی با {lookup_field}={lookup_value} در {model_name} پیدا شد')
			return JsonResponse({
				'status': 'error',
				'message': 'خطای داخلی: چند آیتم با این شناسه پیدا شد.',
				'error_code': 'MULTIPLE_OBJECTS_FOUND'
			}, status=500)
		except ValueError:
			return JsonResponse({
				'status': 'error',
				'message': f'مقدار {lookup_field} نامعتبر است.',
				'error_code': 'INVALID_LOOKUP_VALUE'
			}, status=400)
		
		# ===============================================================
		# بررسی مجوز سطح شی
		# ===============================================================
		if not check_object_permission(request.user, obj, 'view'):
			return JsonResponse({
				'status': 'error',
				'message': 'شما مجوز دسترسی به این آیتم را ندارید.',
				'error_code': 'OBJECT_ACCESS_DENIED'
			}, status=403)
		
		# ===============================================================
		# جمع‌آوری اطلاعات شی
		# ===============================================================
		object_info = _build_object_info(obj, model_class, model_name, request.user)
		
		# ===============================================================
		# استخراج فیلدهای قابل خواندن
		# ===============================================================
		readable_fields = _get_readable_fields(obj, model_class, request.user)
		
		# ===============================================================
		# استخراج فیلدهای قابل نوشتن
		# ===============================================================
		writable_fields = _get_writable_fields(obj, model_class, request.user)
		
		# ===============================================================
		# استخراج عملیات مجاز
		# ===============================================================
		allowed_actions = _get_allowed_actions(obj, model_class, model_name, request.user)
		
		# ===============================================================
		# استخراج روابط
		# ===============================================================
		relationships = _get_relationships(obj, model_class, request.user)
		
		# ===============================================================
		# تولید نمونه‌ها
		# ===============================================================
		examples = _generate_examples(obj, model_name, lookup_value, allowed_actions)
		
		# ===============================================================
		# ساخت پاسخ نهایی
		# ===============================================================
		response_data = {
			'object_info': object_info,
			'readable_fields': readable_fields,
			'writable_fields': writable_fields,
			'allowed_actions': allowed_actions,
			'relationships': relationships,
			'examples': examples,
			'metadata': {
				'retrieved_at': timezone.now().isoformat(),
				'model_name': model_name,
				'lookup_field': lookup_field,
				'lookup_value': lookup_value
			}
		}
		
		logger.info(
			f'کاربر {request.user.id} اطلاعات شی {model_name}:{lookup_value} را مشاهده کرد'
		)
		
		return JsonResponse({
			'status': 'success',
			'data': response_data
		}, status=200)
	
	except Exception as e:
		logger.exception(f'error on object_help برای {model_name}/{lookup_value}')
		return JsonResponse({
			'status': 'error',
			'message': 'خطایی در سرور رخ داده است.',
			'error_code': 'INTERNAL_SERVER_ERROR',
			'debug_info': str(e) if settings.DEBUG else None
		}, status=500)


# ============================================================================
# توابع کمکی برای object_help
# ============================================================================

def _build_object_info(obj, model_class, model_name, user):
	"""
	ساخت اطلاعات هویتی شی
	
	شامل: شناسه، نام‌های نمایشی، تاریخ ایجاد، وضعیت دسترسی و ...
	
	Args:
		obj: شی مورد نظر
		model_class: کلاس مدل
		model_name: نام مدل (string)
		user: کاربر درخواست‌کننده
		
	Returns:
		dict: اطلاعات کامل شی
	"""
	
	verbose_name = model_class._meta.verbose_name
	verbose_name_plural = model_class._meta.verbose_name_plural
	
	# شناسه شی
	pk_value = obj.pk
	
	# __str__ representation
	try:
		str_representation = str(obj)
	except Exception:
		str_representation = f"{model_name} #{pk_value}"
	
	# ===============================================================
	# تاریخ ایجاد و به‌روزرسانی
	# ===============================================================
	created_at = None
	modified_at = None
	
	# جستجوی فیلدهای تاریخ
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		
		# تاریخ ایجاد
		if not created_at and any(keyword in field_name_lower for keyword in ['created', 'create_time', 'date_joined']):
			created_at = getattr(obj, field.name, None)
		
		# تاریخ ویرایش
		if not modified_at and any(keyword in field_name_lower for keyword in ['modified', 'updated', 'last_updated', 'update_time']):
			modified_at = getattr(obj, field.name, None)
	
	# ===============================================================
	# شناسایی کاربر سازنده و به‌روزکننده
	# ===============================================================
	author_id = None
	author_username = None
	modifier_id = None
	modifier_username = None
	
	for field in model_class._meta.fields:
		if field.get_internal_type() == 'ForeignKey':
			field_name_lower = field.name.lower()
			
			# کاربر سازنده
			if not author_id and any(keyword in field_name_lower for keyword in ['author', 'created_by', 'creator', 'owner']):
				author_obj = getattr(obj, field.name, None)
				if author_obj:
					author_id = author_obj.pk
					author_username = getattr(author_obj, 'username', str(author_obj))
			
			# کاربر به‌روزکننده
			if not modifier_id and any(keyword in field_name_lower for keyword in ['modifier', 'updated_by', 'last_modified_by']):
				modifier_obj = getattr(obj, field.name, None)
				if modifier_obj:
					modifier_id = modifier_obj.pk
					modifier_username = getattr(modifier_obj, 'username', str(modifier_obj))
	
	# ===============================================================
	# شناسایی وضعیت شی
	# ===============================================================
	status_value = None
	status_verbose = None
	status_field_name = None
	
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		if any(keyword in field_name_lower for keyword in ['status', 'state']):
			status_field_name = field.name
			status_value = getattr(obj, field.name)
			
			# اگر choices داشته باشد
			if hasattr(field, 'choices') and field.choices:
				status_verbose = dict(field.choices).get(status_value, status_value)
			else:
				status_verbose = status_value
			break
	
	# ===============================================================
	# شناسایی وضعیت فعال/غیرفعال
	# ===============================================================
	is_active = None
	is_active_field_name = None
	
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		if any(keyword in field_name_lower for keyword in ['is_active', 'active', 'enabled', 'is_enabled', 'is_published']):
			is_active_field_name = field.name
			is_active = getattr(obj, field.name)
			break
	
	# ===============================================================
	# اطلاعات مجوزها
	# ===============================================================
	user_permissions = []
	if user.is_authenticated:
		if check_object_permission(user, obj, 'view'):
			user_permissions.append('view')
		if check_object_permission(user, obj, 'update'):
			user_permissions.append('update')
		if check_object_permission(user, obj, 'delete'):
			user_permissions.append('delete')
		
		# مجوزهای سفارشی
		if check_object_permission(user, obj, 'copy'):
			user_permissions.append('copy')
		if check_object_permission(user, obj, 'export'):
			user_permissions.append('export')
		if check_object_permission(user, obj, 'archive'):
			user_permissions.append('archive')
	
	# ===============================================================
	# URLهای مرتبط
	# ===============================================================
	base_api_url = getattr(settings, 'BASE_API_URL', '/api/')
	model_name_lower = model_name.lower()
	
	urls = {
		'detail': f'{base_api_url}{model_name_lower}/{pk_value}/',
		'update': f'{base_api_url}{model_name_lower}/{pk_value}/update/',
		'delete': f'{base_api_url}{model_name_lower}/{pk_value}/delete/',
		'help': f'{base_api_url}{model_name_lower}/{pk_value}/object_help/',
	}
	
	# URLهای شرطی
	if 'copy' in user_permissions:
		urls['copy'] = f'{base_api_url}{model_name_lower}/{pk_value}/copy/'
	if 'export' in user_permissions:
		urls['export'] = f'{base_api_url}{model_name_lower}/{pk_value}/export/'
	
	# ===============================================================
	# فیلدهای قابل جستجو
	# ===============================================================
	searchable_fields = []
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		if any(keyword in field_name_lower for keyword in ['name', 'title', 'code', 'slug', 'email', 'username']):
			searchable_fields.append(field.name)
	
	# ===============================================================
	# شماره‌گذاری و ترتیب
	# ===============================================================
	order_value = None
	order_field_name = None
	
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		if any(keyword in field_name_lower for keyword in ['sort_order', 'order', 'position', 'sort_index', 'ordering']):
			order_field_name = field.name
			order_value = getattr(obj, field.name, None)
			break
	
	# ===============================================================
	# بررسی soft delete
	# ===============================================================
	is_deleted = False
	deleted_at = None
	
	for field in model_class._meta.fields:
		field_name_lower = field.name.lower()
		if any(keyword in field_name_lower for keyword in ['is_deleted', 'deleted']):
			is_deleted = getattr(obj, field.name, False)
		if any(keyword in field_name_lower for keyword in ['deleted_at', 'delete_time']):
			deleted_at = getattr(obj, field.name, None)
	
	# ===============================================================
	# ساخت خروجی نهایی
	# ===============================================================
	return {
		# شناسه و نام
		'id': pk_value,
		'str_representation': str_representation,
		'model_name': model_name,
		'verbose_name': verbose_name,
		'verbose_name_plural': verbose_name_plural,
		
		# تاریخ‌ها
		'created_at': created_at.isoformat() if created_at else None,
		'modified_at': modified_at.isoformat() if modified_at else None,
		
		# کاربران
		'author': {
			'id': author_id,
			'username': author_username,
		} if author_id else None,
		'modifier': {
			'id': modifier_id,
			'username': modifier_username,
		} if modifier_id else None,
		
		# وضعیت
		'status': {
			'field': status_field_name,
			'value': status_value,
			'verbose': status_verbose
		} if status_field_name else None,
		
		'is_active': {
			'field': is_active_field_name,
			'value': is_active
		} if is_active_field_name is not None else None,
		
		# حذف منطقی
		'deletion': {
			'is_deleted': is_deleted,
			'deleted_at': deleted_at.isoformat() if deleted_at else None
		} if is_deleted or deleted_at else None,
		
		# مجوزهای کاربر
		'user_permissions': user_permissions,
		'is_owner': author_id == user.pk if author_id and user.is_authenticated else False,
		
		# URLها
		'urls': urls,
		
		# جستجو و فیلتر
		'searchable_fields': searchable_fields,
		
		# ترتیب
		'order': {
			'field': order_field_name,
			'value': order_value
		} if order_field_name else None,
	}


def _get_readable_fields(obj, model_class, user):
	"""
	استخراج لیست فیلدهای قابل خواندن برای کاربر جاری
	
	فقط فیلدهایی که کاربر مجوز خواندن آن‌ها را دارد.
	
	Args:
		obj: شی مورد نظر
		model_class: کلاس مدل
		user: کاربر درخواست‌کننده
		
	Returns:
		list: لیست فیلدهای قابل خواندن
	"""
	readable_fields = []
	
	# فیلدهای معمولی
	for field in model_class._meta.fields:
		field_name = field.name
		
		# بررسی مجوز خواندن
		if not has_field_permission(user, obj, field_name, 'read'):
			continue
		
		# اطلاعات فیلد
		field_info = _get_field_info(field, obj)
		field_info['permission'] = 'read'
		field_info['current_value'] = _get_field_value(obj, field)
		
		readable_fields.append(field_info)
	
	# فیلدهای Many-to-Many
	for field in model_class._meta.many_to_many:
		field_name = field.name
		
		# بررسی مجوز خواندن
		if not has_field_permission(user, obj, field_name, 'read'):
			continue
		
		# اطلاعات فیلد
		field_info = _get_field_info(field, obj)
		field_info['permission'] = 'read'
		field_info['current_value'] = _get_field_value(obj, field)
		
		readable_fields.append(field_info)
	
	return readable_fields


def _get_writable_fields(obj, model_class, user):
	"""
	استخراج لیست فیلدهای قابل ویرایش برای کاربر جاری
	
	فقط فیلدهایی که کاربر مجوز نوشتن آن‌ها را دارد
	و قابل تغییر هستند (non-auto_now, non-primary key, etc.)
	
	Args:
		obj: شی مورد نظر
		model_class: کلاس مدل
		user: کاربر درخواست‌کننده
		
	Returns:
		list: لیست فیلدهای قابل ویرایش
	"""
	writable_fields = []
	
	for field in model_class._meta.fields:
		field_name = field.name
		
		# فیلدهای خودکار را خارج می‌کنیم
		if field.auto_now or field.auto_now_add:
			continue
		
		# Primary Key معمولاً قابل تغییر نیست
		if field.primary_key:
			continue
		
		# فیلدهای read-only
		if not field.editable:
			continue
		
		# بررسی مجوز نوشتن
		if not has_field_permission(user, obj, field_name, 'write'):
			continue
		
		# اطلاعات فیلد
		field_info = _get_field_info(field, obj)
		field_info['permission'] = 'write'
		field_info['editable'] = True
		field_info['current_value'] = _get_field_value(obj, field)
		
		# اطلاعات validation
		field_info['validation'] = {
			'required': field.blank == False,
			'max_length': getattr(field, 'max_length', None),
			'min_value': getattr(field, 'min_value', None) if hasattr(field, 'min_value') else None,
			'max_value': getattr(field, 'max_value', None) if hasattr(field, 'max_value') else None,
		}
		
		writable_fields.append(field_info)
	
	# فیلدهای Many-to-Many
	for field in model_class._meta.many_to_many:
		field_name = field.name
		
		# بررسی مجوز نوشتن
		if not has_field_permission(user, obj, field_name, 'write'):
			continue
		
		# اطلاعات فیلد
		field_info = _get_field_info(field, obj)
		field_info['permission'] = 'write'
		field_info['editable'] = True
		field_info['current_value'] = _get_field_value(obj, field)
		
		writable_fields.append(field_info)
	
	return writable_fields


def _get_allowed_actions(obj, model_class, model_name, user):
	"""
	استخراج لیست عملیات مجاز روی شی
	
	شامل: update, delete, copy, duplicate, export, archive, restore
	
	Args:
		obj: شی مورد نظر
		model_class: کلاس مدل
		model_name: نام مدل
		user: کاربر درخواست‌کننده
		
	Returns:
		list: لیست عملیات مجاز
	"""
	
	base_api_url = getattr(settings, 'BASE_API_URL', '/api/')
	model_name_lower = model_name.lower()
	pk = obj.pk
	
	allowed_actions = []
	
	# ===============================================================
	# View (همیشه موجود است چون کاربر اینجا را باز کرده)
	# ===============================================================
	allowed_actions.append({
		'action': 'view',
		'label': 'مشاهده جزئیات',
		'method': 'GET',
		'endpoint': f'{base_api_url}{model_name_lower}/{pk}/',
		'description': 'دریافت اطلاعات کامل این شی',
		'icon': 'eye',
		'color': 'blue',
	})
	
	# ===============================================================
	# Update
	# ===============================================================
	if check_object_permission(user, obj, 'update'):
		allowed_actions.append({
			'action': 'update',
			'label': 'ویرایش',
			'method': 'PUT',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/update/',
			'description': 'ویرایش فیلدهای قابل تغییر',
			'icon': 'edit',
			'color': 'green',
			'required_fields': [
				f['name'] for f in _get_writable_fields(obj, model_class, user)
				if f.get('validation', {}).get('required', False)
			],
		})
	
	# ===============================================================
	# Partial Update
	# ===============================================================
	if check_object_permission(user, obj, 'update'):
		allowed_actions.append({
			'action': 'partial_update',
			'label': 'ویرایش جزئی',
			'method': 'PATCH',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/update/',
			'description': 'ویرایش تنها برخی از فیلدها',
			'icon': 'edit',
			'color': 'green',
		})
	
	# ===============================================================
	# Delete
	# ===============================================================
	if check_object_permission(user, obj, 'delete'):
		# بررسی soft delete
		has_soft_delete = any(
			f.name.lower() in ['is_deleted', 'deleted', 'deleted_at']
			for f in model_class._meta.fields
		)
		
		allowed_actions.append({
			'action': 'delete',
			'label': 'حذف' if has_soft_delete else 'حذف دائم',
			'method': 'DELETE',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/delete/',
			'description': 'حذف منطقی این شی' if has_soft_delete else 'حذف کامل این شی از دیتابیس',
			'icon': 'trash',
			'color': 'red',
			'warning': 'این عمل غیرقابل بازگشت است' if not has_soft_delete else 'می‌توانید بعداً بازگردانی کنید',
			'requires_confirmation': True,
		})
	
	# ===============================================================
	# Copy/Duplicate
	# ===============================================================
	if check_object_permission(user, obj, 'copy'):
		allowed_actions.append({
			'action': 'copy',
			'label': 'کپی',
			'method': 'POST',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/copy/',
			'description': 'ایجاد یک کپی از این شی',
			'icon': 'copy',
			'color': 'purple',
			'returns': 'شناسه شی جدید',
		})
	
	# ===============================================================
	# Export
	# ===============================================================
	if check_object_permission(user, obj, 'export'):
		allowed_actions.append({
			'action': 'export',
			'label': 'خروجی',
			'method': 'GET',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/export/',
			'description': 'دریافت خروجی از این شی',
			'icon': 'download',
			'color': 'indigo',
			'formats': ['json', 'csv', 'xlsx', 'pdf'],
			'query_params': {
				'format': 'نوع فایل خروجی (json, csv, xlsx, pdf)'
			}
		})
	
	# ===============================================================
	# Archive (اگر فیلد archived وجود داشته باشد)
	# ===============================================================
	has_archive_field = any(
		f.name.lower() in ['is_archived', 'archived', 'archived_at']
		for f in model_class._meta.fields
	)
	
	if has_archive_field and check_object_permission(user, obj, 'archive'):
		is_archived = any(
			getattr(obj, f.name, False)
			for f in model_class._meta.fields
			if f.name.lower() in ['is_archived', 'archived']
		)
		
		if not is_archived:
			allowed_actions.append({
				'action': 'archive',
				'label': 'بایگانی',
				'method': 'POST',
				'endpoint': f'{base_api_url}{model_name_lower}/{pk}/archive/',
				'description': 'انتقال به بایگانی',
				'icon': 'archive',
				'color': 'yellow',
			})
		else:
			allowed_actions.append({
				'action': 'unarchive',
				'label': 'خروج از بایگانی',
				'method': 'POST',
				'endpoint': f'{base_api_url}{model_name_lower}/{pk}/unarchive/',
				'description': 'بازگردانی از بایگانی',
				'icon': 'archive',
				'color': 'yellow',
			})
	
	# ===============================================================
	# Restore (اگر soft delete شده باشد)
	# ===============================================================
	is_deleted = any(
		getattr(obj, f.name, False)
		for f in model_class._meta.fields
		if f.name.lower() in ['is_deleted', 'deleted']
	)
	
	if is_deleted and check_object_permission(user, obj, 'restore'):
		allowed_actions.append({
			'action': 'restore',
			'label': 'بازیابی',
			'method': 'POST',
			'endpoint': f'{base_api_url}{model_name_lower}/{pk}/restore/',
			'description': 'بازگردانی از حالت حذف شده',
			'icon': 'refresh',
			'color': 'teal',
		})
	
	return allowed_actions


def _get_relationships(obj, model_class, user):
	"""
	استخراج روابط شی با سایر مدل‌ها
	
	شامل: ForeignKey, ManyToManyField, OneToOneField, Reverse Relations
	
	Args:
		obj: شی مورد نظر
		model_class: کلاس مدل
		user: کاربر درخواست‌کننده
		
	Returns:
		dict: روابط شی
	"""
	relationships = {
		'foreign_keys': [],
		'many_to_many': [],
		'one_to_one': [],
		'reverse_relations': [],
	}
	
	# ===============================================================
	# روابط ForeignKey و OneToOne (روابط رو به جلو)
	# ===============================================================
	for field in model_class._meta.fields:
		internal_type = field.get_internal_type()
		
		# فیلدهای رابطه‌ای
		if internal_type not in ('ForeignKey', 'OneToOneField'):
			continue
		
		# بررسی مجوز خواندن این فیلد
		if not has_field_permission(user, obj, field.name, 'read'):
			continue
		
		related_model = field.related_model
		related_obj = getattr(obj, field.name, None)
		
		# اطلاعات مدل مرتبط
		related_info = {
			'field_name': field.name,
			'verbose_name': str(field.verbose_name),
			'related_model': related_model.__name__,
			'related_verbose_name': str(related_model._meta.verbose_name),
			'on_delete': _get_on_delete_behavior(field),
			'null': field.null,
			'value': None,
		}
		
		# اگر شی مرتبط وجود داشته باشد
		if related_obj is not None:
			related_info['value'] = {
				'id': related_obj.pk,
				'str': str(related_obj),
				'url': _build_related_url(related_model, related_obj.pk),
			}
		
		# دسته‌بندی بر اساس نوع
		if internal_type == 'OneToOneField':
			relationships['one_to_one'].append(related_info)
		else:
			relationships['foreign_keys'].append(related_info)
	
	# ===============================================================
	# روابط Many-to-Many (روابط رو به جلو)
	# ===============================================================
	for field in model_class._meta.many_to_many:
		# بررسی مجوز خواندن این فیلد
		if not has_field_permission(user, obj, field.name, 'read'):
			continue
		
		related_model = field.related_model
		
		# دریافت شی‌های مرتبط (با محدودیت تعداد برای عملکرد بهتر)
		try:
			related_manager = getattr(obj, field.name)
			total_count = related_manager.count()
			sample_objects = related_manager.all()[:10]
			
			related_values = [
				{
					'id': item.pk,
					'str': str(item),
					'url': _build_related_url(related_model, item.pk),
				}
				for item in sample_objects
			]
		except Exception:
			total_count = 0
			related_values = []
		
		relationships['many_to_many'].append({
			'field_name': field.name,
			'verbose_name': str(field.verbose_name),
			'related_model': related_model.__name__,
			'related_verbose_name': str(related_model._meta.verbose_name_plural),
			'total_count': total_count,
			'sample': related_values,
			'truncated': total_count > 10,
		})
	
	# ===============================================================
	# روابط معکوس (Reverse Relations)
	# ===============================================================
	for relation in model_class._meta.related_objects:
		related_model = relation.related_model
		accessor_name = relation.get_accessor_name()
		
		# بررسی دسترسی به مدل مرتبط
		if not has_model_permission(user, related_model, 'view'):
			continue
		
		try:
			# رابطه یک به چند یا چند به چند معکوس
			if relation.one_to_many or relation.many_to_many:
				reverse_manager = getattr(obj, accessor_name, None)
				if reverse_manager is None:
					continue
				
				total_count = reverse_manager.count()
				sample_objects = reverse_manager.all()[:5]
				
				sample_values = [
					{
						'id': item.pk,
						'str': str(item),
						'url': _build_related_url(related_model, item.pk),
					}
					for item in sample_objects
				]
				
				relationships['reverse_relations'].append({
					'accessor_name': accessor_name,
					'related_model': related_model.__name__,
					'related_verbose_name': str(related_model._meta.verbose_name_plural),
					'relation_type': 'many_to_many' if relation.many_to_many else 'one_to_many',
					'total_count': total_count,
					'sample': sample_values,
					'truncated': total_count > 5,
				})
			
			# رابطه یک به یک معکوس
			elif relation.one_to_one:
				try:
					reverse_obj = getattr(obj, accessor_name, None)
				except related_model.DoesNotExist:
					reverse_obj = None
				
				relationships['reverse_relations'].append({
					'accessor_name': accessor_name,
					'related_model': related_model.__name__,
					'related_verbose_name': str(related_model._meta.verbose_name),
					'relation_type': 'one_to_one',
					'value': {
						'id': reverse_obj.pk,
						'str': str(reverse_obj),
						'url': _build_related_url(related_model, reverse_obj.pk),
					} if reverse_obj else None,
				})
		
		except Exception as e:
			logger.debug(f'خطا در پردازش رابطه معکوس {accessor_name}: {e}')
			continue
	
	return relationships


def _generate_examples(model_name, model_class, readable_fields, writable_fields, callable_methods, lookup_field):
	"""
	تولید نمونه درخواست‌ها و پاسخ‌ها برای عملیات مجاز
	
	این نمونه‌ها به توسعه‌دهنده کمک می‌کنند تا نحوه استفاده از API را درک کند.
	
	Args:
		model_name: نام مدل
		model_class: کلاس مدل
		readable_fields: لیست فیلدهای خواندنی
		writable_fields: لیست فیلدهای نوشتنی
		callable_methods: لیست متدهای قابل فراخوانی
		lookup_field: فیلد lookup (مثلاً 'id' یا 'slug')
		
	Returns:
		dict: نمونه‌های درخواست/پاسخ
	"""
	
	base_api_url = getattr(settings, 'BASE_API_URL', '/api/v1/')
	model_name_lower = model_name.lower()
	
	examples = {}
	
	# مقدار نمونه برای lookup_field
	lookup_example = '<id>' if lookup_field == 'pk' or lookup_field == 'id' else f'<{lookup_field}>'
	
	# ===============================================================
	# نمونه GET (لیست)
	# ===============================================================
	examples['list'] = {
		'description': f'دریافت لیست {model_class._meta.verbose_name_plural}',
		'request': {
			'method': 'GET',
			'url': f'{base_api_url}{model_name_lower}/',
			'headers': {
				'Authorization': 'Bearer <your_token>',
				'Accept': 'application/json',
			},
			'query_params': {
				'page': 1,
				'page_size': 20,
				'fields': ','.join([f['name'] for f in readable_fields[:3]]) if readable_fields else 'id',
			}
		},
		'response': {
			'status_code': 200,
			'body': {
				'status': 'success',
				'data': {
					'count': 10,
					'next': f'{base_api_url}{model_name_lower}/?page=2',
					'previous': None,
					'results': [
						{f['name']: f.get('example', '...') for f in readable_fields[:3]}
					]
				},
			},
		},
	}
	
	# ===============================================================
	# نمونه GET (جزئیات)
	# ===============================================================
	examples['retrieve'] = {
		'description': f'دریافت اطلاعات یک {model_class._meta.verbose_name}',
		'request': {
			'method': 'GET',
			'url': f'{base_api_url}{model_name_lower}/{lookup_example}/',
			'headers': {
				'Authorization': 'Bearer <your_token>',
				'Accept': 'application/json',
			},
		},
		'response': {
			'status_code': 200,
			'body': {
				'status': 'success',
				'data': {f['name']: f.get('example', '...') for f in readable_fields[:5]},
			},
		},
	}
	
	# ===============================================================
	# نمونه POST (ایجاد)
	# ===============================================================
	if writable_fields:
		create_body = {}
		for field in writable_fields[:5]:
			if not field.get('read_only', False):
				create_body[field['name']] = field.get('example', '...')
		
		examples['create'] = {
			'description': f'ایجاد {model_class._meta.verbose_name} جدید',
			'request': {
				'method': 'POST',
				'url': f'{base_api_url}{model_name_lower}/',
				'headers': {
					'Authorization': 'Bearer <your_token>',
					'Content-Type': 'application/json',
				},
				'body': create_body,
			},
			'response': {
				'status_code': 201,
				'body': {
					'status': 'success',
					'message': 'با موفقیت ایجاد شد.',
					'data': {'id': 123, **create_body},
				},
			},
		}
	
	# ===============================================================
	# نمونه PUT (ویرایش کامل)
	# ===============================================================
	if writable_fields:
		update_body = {}
		for field in writable_fields[:5]:
			if not field.get('read_only', False):
				update_body[field['name']] = f"مقدار جدید {field['name']}"
		
		examples['update'] = {
			'description': f'ویرایش کامل {model_class._meta.verbose_name}',
			'request': {
				'method': 'PUT',
				'url': f'{base_api_url}{model_name_lower}/{lookup_example}/',
				'headers': {
					'Authorization': 'Bearer <your_token>',
					'Content-Type': 'application/json',
				},
				'body': update_body,
			},
			'response': {
				'status_code': 200,
				'body': {
					'status': 'success',
					'message': 'با موفقیت به‌روزرسانی شد.',
					'data': {'id': lookup_example, **update_body},
				},
			},
		}
	
	# ===============================================================
	# نمونه PATCH (ویرایش جزئی)
	# ===============================================================
	if writable_fields:
		patch_body = {writable_fields[0]['name']: 'مقدار جدید'}
		
		examples['partial_update'] = {
			'description': f'ویرایش جزئی {model_class._meta.verbose_name}',
			'request': {
				'method': 'PATCH',
				'url': f'{base_api_url}{model_name_lower}/{lookup_example}/',
				'headers': {
					'Authorization': 'Bearer <your_token>',
					'Content-Type': 'application/json',
				},
				'body': patch_body,
			},
			'response': {
				'status_code': 200,
				'body': {
					'status': 'success',
					'message': 'با موفقیت به‌روزرسانی شد.',
				},
			},
		}
	
	# ===============================================================
	# نمونه DELETE (حذف)
	# ===============================================================
	examples['delete'] = {
		'description': f'حذف نرم {model_class._meta.verbose_name}',
		'request': {
			'method': 'DELETE',
			'url': f'{base_api_url}{model_name_lower}/{lookup_example}/',
			'headers': {
				'Authorization': 'Bearer <your_token>',
			},
		},
		'response': {
			'status_code': 204,
			'body': None,
		},
	}
	
	examples['permanent_delete'] = {
		'description': f'حذف دائمی {model_class._meta.verbose_name}',
		'request': {
			'method': 'DELETE',
			'url': f'{base_api_url}{model_name_lower}/{lookup_example}/?permanent=true',
			'headers': {
				'Authorization': 'Bearer <your_token>',
			},
		},
		'response': {
			'status_code': 204,
			'body': None,
		},
		'warning': '⚠️ این عملیات غیرقابل بازگشت است!'
	}
	
	# ===============================================================
	# نمونه‌های متدهای سفارشی
	# ===============================================================
	for method in callable_methods:
		method_name = method['name']
		method_params = method.get('parameters', [])
		
		# ساخت نمونه بدنه درخواست
		method_body = {}
		for param in method_params:
			if param.get('required', False):
				method_body[param['name']] = param.get('example', '...')
		
		examples[f'method_{method_name}'] = {
			'description': method.get('description', f'فراخوانی متد {method_name}'),
			'request': {
				'method': 'POST',
				'url': f'{base_api_url}{model_name_lower}/{lookup_example}/call/{method_name}/',
				'headers': {
					'Authorization': 'Bearer <your_token>',
					'Content-Type': 'application/json',
				},
				'body': method_body if method_body else None,
			},
			'response': {
				'status_code': 200,
				'body': {
					'status': 'success',
					'message': f'متد {method_name} با موفقیت اجرا شد.',
					'data': method.get('return_example', None),
				},
			},
		}
	
	return examples

def _get_field_info(field, obj):
	"""
	استخراج اطلاعات ساختاری یک فیلد
	
	Args:
		field: فیلد مدل
		obj: شی مورد نظر
		
	Returns:
		dict: اطلاعات فیلد
	"""
	info = {
		'name': field.name,
		'verbose_name': str(field.verbose_name),
		'type': field.get_internal_type(),
		'help_text': str(field.help_text) if field.help_text else None,
		'required': not field.blank and not field.null,
		'editable': field.editable,
		'max_length': getattr(field, 'max_length', None),
	}
	
	# افزودن default اگر قابل نمایش باشد
	if hasattr(field, 'default') and field.default is not NOT_PROVIDED:
		if not callable(field.default):
			info['default'] = field.default
		else:
			info['default'] = '<dynamic>'
	
	# افزودن choices اگر وجود داشته باشد
	if hasattr(field, 'choices') and field.choices:
		info['choices'] = [
			{'value': choice[0], 'display': str(choice[1])}
			for choice in field.choices
		]
	
	# اطلاعات اعتبارسنجی
	validators_info = []
	if hasattr(field, 'validators'):
		for validator in field.validators:
			validator_name = validator.__class__.__name__
			validators_info.append(validator_name)
	
	if validators_info:
		info['validators'] = validators_info
	
	return info


def _get_field_value(obj, field):
	"""
	استخراج مقدار یک فیلد با در نظر گرفتن نوع
	
	Args:
		obj: شی مورد نظر
		field: فیلد مدل
		
	Returns:
		مقدار فیلد به صورت قابل سریالایز
	"""
	try:
		val = getattr(obj, field.name)
		
		# مقادیر None
		if val is None:
			return None
		
		# مدیریت فیلدهای Choices
		if hasattr(field, 'choices') and field.choices:
			display_method = f'get_{field.name}_display'
			if hasattr(obj, display_method):
				return {
					'value': val,
					'display': getattr(obj, display_method)(),
				}
		
		# مدیریت تاریخ و زمان
		if hasattr(val, 'isoformat'):
			return val.isoformat()
		
		# مدیریت فایل‌ها
		if hasattr(val, 'url'):
			return {
				'url': val.url,
				'name': val.name,
				'size': val.size if hasattr(val, 'size') else None,
			}
		
		# مدیریت JSONField
		if isinstance(val, (dict, list)):
			return val
		
		# مدیریت بولین
		if isinstance(val, bool):
			return val
		
		# مدیریت اعداد
		if isinstance(val, (int, float, Decimal)):
			return float(val) if isinstance(val, Decimal) else val
		
		# سایر مقادیر به صورت رشته
		return str(val)
		
	except Exception as e:
		logger.debug(f'خطا در استخراج مقدار فیلد {field.name}: {e}')
		return None


def _get_on_delete_behavior(field):
	"""
	دریافت رفتار on_delete برای نمایش در API
	
	Args:
		field: فیلد ForeignKey یا OneToOne
		
	Returns:
		str: نام رفتار on_delete
	"""
	if hasattr(field, 'remote_field') and field.remote_field:
		on_delete = field.remote_field.on_delete
		if on_delete:
			return on_delete.__name__
	return 'CASCADE'  # مقدار پیش‌فرض


def _build_related_url(model, pk):
	"""
	ساخت URL برای شی مرتبط
	
	Args:
		model: کلاس مدل
		pk: کلید اصلی شی
		
	Returns:
		str: URL شی مرتبط
	"""
	
	base_api_url = getattr(settings, 'BASE_API_URL', '/api/')
	model_name = model._meta.model_name
	
	return f'{base_api_url}{model_name}/{pk}/'


def _get_metadata(model_class, obj):
	"""
	استخراج متادیتای مدل
	
	Args:
		model_class: کلاس مدل
		obj: شی مورد نظر
		
	Returns:
		dict: متادیتای مدل
	"""
	meta = model_class._meta
	
	metadata = {
		'app_label': meta.app_label,
		'model_name': meta.model_name,
		'verbose_name': str(meta.verbose_name),
		'verbose_name_plural': str(meta.verbose_name_plural),
		'abstract': meta.abstract,
		'db_table': meta.db_table,
	}
	
	# اطلاعات ordering
	if meta.ordering:
		metadata['ordering'] = list(meta.ordering)
	
	# اطلاعات unique_together
	if meta.unique_together:
		metadata['unique_together'] = [list(fields) for fields in meta.unique_together]
	
	# اطلاعات indexes
	if hasattr(meta, 'indexes') and meta.indexes:
		metadata['indexes'] = [
			{
				'name': index.name,
				'fields': list(index.fields),
			}
			for index in meta.indexes
		]
	
	# اطلاعات permissions
	if hasattr(meta, 'permissions') and meta.permissions:
		metadata['custom_permissions'] = [
			{'codename': perm[0], 'name': perm[1]}
			for perm in meta.permissions
		]
	
	return metadata


# ===============================================================
# پایان فایل views.py
# ===============================================================

def model_detail(request, model_name, lookup):
	"""
	دریافت، بروزرسانی یا حذف یک آیتم خاص از مدل
	
	این endpoint امکان عملیات CRUD روی یک instance خاص را فراهم می‌کند:
	- GET: دریافت جزئیات آیتم
	- POST: بروزرسانی آیتم (کامل یا جزئی)
	- DELETE: حذف آیتم (نرم یا دائمی)
	
	Args:
		request: درخواست HTTP
		model_name: نام مدل (case-insensitive)
		lookup: مقدار lookup (می‌تواند ID، UUID، slug و غیره باشد)
		
	Query Parameters (DELETE):
		permanent: حذف دائمی (true/false، پیش‌فرض: false)
		
	Returns:
		Response: جزئیات آیتم یا نتیجه عملیات
	"""
	# ===============================================================
	# پیدا کردن مدل
	# ===============================================================
	model_class = None
	model_name_lower = model_name.lower()
	
	for app_config in apps.get_app_configs():
		try:
			found_model = apps.get_model(app_config.label, model_name)
			if found_model._meta.model_name == model_name_lower:
				model_class = found_model
				break
		except LookupError:
			continue
	
	if model_class is None:
		return Response(
			{
				'status': 'error',
				'message': f'مدل "{model_name}" یافت نشد.',
				'code': 'MODEL_NOT_FOUND',
			},
			status=status.HTTP_404_NOT_FOUND
		)
	
	# ===============================================================
	# تعیین فیلد lookup
	# ===============================================================
	lookup_field = getattr(model_class, 'API_LOOKUP_FIELD', 'pk')
	
	# تلاش برای تشخیص نوع lookup
	lookup_kwargs = {}
	
	# UUID
	if lookup_field == 'pk' and hasattr(model_class._meta.pk, 'get_internal_type'):
		if model_class._meta.pk.get_internal_type() == 'UUIDField':
			try:
				lookup_value = uuid.UUID(lookup)
				lookup_kwargs[lookup_field] = lookup_value
			except ValueError:
				return Response(
					{
						'status': 'error',
						'message': 'فرمت UUID نامعتبر است.',
						'code': 'INVALID_UUID',
					},
					status=status.HTTP_400_BAD_REQUEST
				)
		else:
			# Integer PK
			try:
				lookup_kwargs[lookup_field] = int(lookup)
			except ValueError:
				return Response(
					{
						'status': 'error',
						'message': 'فرمت شناسه نامعتبر است.',
						'code': 'INVALID_ID',
					},
					status=status.HTTP_400_BAD_REQUEST
				)
	else:
		# سایر فیلدها (slug, username, etc.)
		lookup_kwargs[lookup_field] = lookup
	
	# ===============================================================
	# دریافت instance
	# ===============================================================
	try:
		instance = model_class.objects.get(**lookup_kwargs)
	except model_class.DoesNotExist:
		return Response(
			{
				'status': 'error',
				'message': f'آیتم با {lookup_field}={lookup} یافت نشد.',
				'code': 'NOT_FOUND',
			},
			status=status.HTTP_404_NOT_FOUND
		)
	except model_class.MultipleObjectsReturned:
		return Response(
			{
				'status': 'error',
				'message': 'چندین آیتم با این شناسه یافت شد.',
				'code': 'MULTIPLE_OBJECTS',
			},
			status=status.HTTP_400_BAD_REQUEST
		)
	
	# ===============================================================
	# GET: دریافت جزئیات آیتم
	# ===============================================================
	if request.method == 'GET':
		# بررسی مجوز
		if not has_model_permission(request.user, model_class, 'view'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز مشاهده این مدل را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		if not check_object_permission(request.user, instance, 'view'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز مشاهده این آیتم را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		# Rate limiting
		try:
			check_rate_limit(
				request,
				f'view_{model_name}',
				max_requests=DEFAULT_RATE_LIMIT_MAX_REQUESTS,
				window_seconds=DEFAULT_RATE_LIMIT_WINDOW_SECONDS
			)
		except RateLimitError as e:
			return Response(
				{
					'status': 'error',
					'message': str(e),
					'code': 'RATE_LIMIT_EXCEEDED',
				},
				status=status.HTTP_429_TOO_MANY_REQUESTS
			)
		
		# سریالایز کردن instance
		serialized_data = {}
		meta = model_class._meta
		
		# فیلدهای معمولی
		for field in meta.fields:
			if not has_field_permission(request.user, instance, field.name, 'read'):
				continue
			
			try:
				value = getattr(instance, field.name)
				
				# ForeignKey و OneToOne
				if field.get_internal_type() in ('ForeignKey', 'OneToOneField'):
					if value is not None:
						serialized_data[field.name] = {
							'id': value.pk,
							'display': str(value),
							'model': value.__class__.__name__,
						}
					else:
						serialized_data[field.name] = None
				# DateTimeField, DateField, TimeField
				elif hasattr(value, 'isoformat'):
					serialized_data[field.name] = value.isoformat()
				# Decimal
				elif isinstance(value, Decimal):
					serialized_data[field.name] = float(value)
				# UUID
				elif hasattr(value, 'hex'):
					serialized_data[field.name] = str(value)
				# سایر موارد
				else:
					serialized_data[field.name] = value
					
			except Exception as e:
				logger.warning(f'خطا در سریالایز فیلد {field.name}: {e}')
				serialized_data[field.name] = None
		
		# فیلدهای ManyToMany
		m2m_data = {}
		for field in meta.many_to_many:
			if not has_field_permission(request.user, instance, field.name, 'read'):
				continue
			
			try:
				related_objects = getattr(instance, field.name).all()
				m2m_data[field.name] = [
					{
						'id': obj.pk,
						'display': str(obj),
					}
					for obj in related_objects
				]
			except Exception as e:
				logger.warning(f'خطا در سریالایز M2M {field.name}: {e}')
				m2m_data[field.name] = []
		
		if m2m_data:
			serialized_data['many_to_many'] = m2m_data
		
		# روابط معکوس
		reverse_relations = {}
		for relation in meta.related_objects:
			accessor_name = relation.get_accessor_name()
			
			# بررسی مجوز
			related_model = relation.related_model
			if not has_model_permission(request.user, related_model, 'view'):
				continue
			
			try:
				if relation.one_to_one:
					# OneToOne reverse
					related_obj = getattr(instance, accessor_name, None)
					if related_obj:
						reverse_relations[accessor_name] = {
							'id': related_obj.pk,
							'display': str(related_obj),
						}
					else:
						reverse_relations[accessor_name] = None
				else:
					# ForeignKey reverse (one-to-many)
					related_objects = getattr(instance, accessor_name).all()
					reverse_relations[accessor_name] = {
						'count': related_objects.count(),
						'items': [
							{
								'id': obj.pk,
								'display': str(obj),
							}
							for obj in related_objects[:5]  # محدود به 5 آیتم اول
						]
					}
			except Exception as e:
				logger.warning(f'خطا در سریالایز reverse relation {accessor_name}: {e}')
		
		if reverse_relations:
			serialized_data['reverse_relations'] = reverse_relations
		
		return Response(
			{
				'status': 'success',
				'data': serialized_data,
				'model': model_class.__name__,
				'timestamp': timezone.now().isoformat(),
			},
			status=status.HTTP_200_OK
		)
	
	# ===============================================================
	# POST: بروزرسانی آیتم
	# ===============================================================
	elif request.method == 'POST':
		# بررسی مجوز
		if not has_model_permission(request.user, model_class, 'change'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز ویرایش این مدل را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		if not check_object_permission(request.user, instance, 'change'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز ویرایش این آیتم را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		# Rate limiting
		try:
			check_rate_limit(
				request,
				key=f'update_{model_name}',
				max_requests=30,
				window_seconds=60
			)
		except RateLimitError as e:
			return Response(
				{
					'status': 'error',
					'message': str(e),
					'code': 'RATE_LIMIT_EXCEEDED',
				},
				status=status.HTTP_429_TOO_MANY_REQUESTS
			)
		
		# دریافت و اعتبارسنجی داده‌ها
		data = request.data
		if not data:
			return Response(
				{
					'status': 'error',
					'message': 'هیچ داده‌ای برای بروزرسانی ارسال نشده است.',
					'code': 'NO_DATA',
				},
				status=status.HTTP_400_BAD_REQUEST
			)
		
		meta = model_class._meta
		updated_fields = []
		errors = {}
		
		# بروزرسانی فیلدها
		for field_name, field_value in data.items():
			# پیدا کردن فیلد
			try:
				field = meta.get_field(field_name)
			except FieldDoesNotExist:
				# بررسی M2M
				if field_name in [f.name for f in meta.many_to_many]:
					field = meta.get_field(field_name)
				else:
					errors[field_name] = 'این فیلد در مدل وجود ندارد.'
					continue
			
			# بررسی editable بودن
			if not field.editable:
				errors[field_name] = 'این فیلد قابل ویرایش نیست.'
				continue
			
			# بررسی مجوز فیلد
			if not has_field_permission(request.user, instance, field_name, 'write'):
				errors[field_name] = 'شما مجوز ویرایش این فیلد را ندارید.'
				continue
			
			# بروزرسانی فیلد
			try:
				# ManyToMany
				if field.many_to_many:
					if not isinstance(field_value, list):
						errors[field_name] = 'مقدار باید یک لیست باشد.'
						continue
					
					related_model = field.related_model
					related_objects = []
					
					for item_id in field_value:
						try:
							related_obj = related_model.objects.get(pk=item_id)
							related_objects.append(related_obj)
						except related_model.DoesNotExist:
							errors[field_name] = f'آیتم با id={item_id} یافت نشد.'
							break
					else:
						# تنظیم M2M (بعد از save)
						getattr(instance, field_name).set(related_objects)
						updated_fields.append(field_name)
				
				# ForeignKey
				elif field.get_internal_type() == 'ForeignKey':
					if field_value is None:
						if not field.null:
							errors[field_name] = 'این فیلد نمی‌تواند خالی باشد.'
							continue
						setattr(instance, field_name, None)
						updated_fields.append(field_name)
					else:
						related_model = field.related_model
						try:
							related_obj = related_model.objects.get(pk=field_value)
							setattr(instance, field_name, related_obj)
							updated_fields.append(field_name)
						except related_model.DoesNotExist:
							errors[field_name] = f'آیتم مرتبط با id={field_value} یافت نشد.'
				
				# سایر فیلدها
				else:
					# تبدیل نوع
					internal_type = field.get_internal_type()
					
					if internal_type == 'DateTimeField':
						field_value = parser.parse(field_value)
					elif internal_type == 'DateField':
						field_value = parser.parse(field_value).date()
					elif internal_type == 'TimeField':
						field_value = parser.parse(field_value).time()
					elif internal_type == 'BooleanField':
						field_value = bool(field_value)
					elif internal_type == 'IntegerField':
						field_value = int(field_value)
					elif internal_type == 'FloatField':
						field_value = float(field_value)
					elif internal_type == 'DecimalField':
						field_value = Decimal(str(field_value))
					elif internal_type == 'UUIDField':
						if not isinstance(field_value, uuid.UUID):
							field_value = uuid.UUID(field_value)
					
					setattr(instance, field_name, field_value)
					updated_fields.append(field_name)
					
			except (ValueError, TypeError, ValidationError) as e:
				errors[field_name] = str(e)
			except Exception as e:
				logger.error(f'خطا در بروزرسانی فیلد {field_name}: {e}', exc_info=True)
				errors[field_name] = 'خطا در پردازش مقدار.'
		
		# بررسی خطاها
		if errors:
			return Response(
				{
					'status': 'error',
					'message': 'خطاهایی در اعتبارسنجی داده‌ها رخ داد.',
					'errors': errors,
					'code': 'VALIDATION_ERROR',
				},
				status=status.HTTP_400_BAD_REQUEST
			)
		
		# ذخیره تغییرات
		try:
			instance.full_clean()  # اعتبارسنجی مدل
			instance.save()
			
			logger.info(
				f'کاربر {request.user.username} آیتم {instance.pk} از مدل {model_class.__name__} را بروزرسانی کرد. '
				f'فیلدهای بروزرسانی شده: {", ".join(updated_fields)}'
			)
			
			return Response(
				{
					'status': 'success',
					'message': 'آیتم با موفقیت بروزرسانی شد.',
					'data': {
						'id': instance.pk,
						'updated_fields': updated_fields,
					},
					'timestamp': timezone.now().isoformat(),
				},
				status=status.HTTP_200_OK
			)
			
		except ValidationError as e:
			return Response(
				{
					'status': 'error',
					'message': 'خطا در اعتبارسنجی داده‌ها.',
					'errors': e.message_dict if hasattr(e, 'message_dict') else {'non_field_errors': [str(e)]},
					'code': 'VALIDATION_ERROR',
				},
				status=status.HTTP_400_BAD_REQUEST
			)
		except Exception as e:
			logger.error(f'خطا در ذخیره آیتم: {e}', exc_info=True)
			return Response(
				{
					'status': 'error',
					'message': 'خطا در ذخیره تغییرات.',
					'code': 'SAVE_ERROR',
					'details': str(e) if settings.DEBUG else None,
				},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR
			)
	
	# ===============================================================
	# DELETE: حذف آیتم
	# ===============================================================
	elif request.method == 'DELETE':
		# بررسی مجوز
		if not has_model_permission(request.user, model_class, 'delete'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز حذف این مدل را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		if not check_object_permission(request.user, instance, 'delete'):
			return Response(
				{
					'status': 'error',
					'message': 'شما مجوز حذف این آیتم را ندارید.',
					'code': 'PERMISSION_DENIED',
				},
				status=status.HTTP_403_FORBIDDEN
			)
		
		# Rate limiting
		try:
			check_rate_limit(
				request,
				key=f'delete_{model_name}',
				max_requests=20,
				window_seconds=60
			)
		except RateLimitError as e:
			return Response(
				{
					'status': 'error',
					'message': str(e),
					'code': 'RATE_LIMIT_EXCEEDED',
				},
				status=status.HTTP_429_TOO_MANY_REQUESTS
			)
		
		# بررسی نوع حذف (نرم یا دائمی)
		permanent = request.query_params.get('permanent', 'false').lower() == 'true'
		
		try:
			if permanent:
				# حذف دائمی
				instance_id = instance.pk
				instance_str = str(instance)
				instance.delete()
				
				logger.warning(
					f'کاربر {request.user.username} آیتم {instance_id} ({instance_str}) '
					f'از مدل {model_class.__name__} را به صورت دائمی حذف کرد.'
				)
				
				return Response(
					{
						'status': 'success',
						'message': 'آیتم به صورت دائمی حذف شد.',
						'deletion_type': 'permanent',
						'timestamp': timezone.now().isoformat(),
					},
					status=status.HTTP_200_OK
				)
			else:
				# حذف نرم (soft delete)
				if hasattr(instance, 'is_deleted') and hasattr(instance, 'deleted_at'):
					instance.is_deleted = True
					instance.deleted_at = timezone.now()
					if hasattr(instance, 'deleted_by'):
						instance.deleted_by = request.user
					instance.save()
					
					logger.info(
						f'کاربر {request.user.username} آیتم {instance.pk} '
						f'از مدل {model_class.__name__} را به صورت نرم حذف کرد.'
					)
					
					return Response(
						{
							'status': 'success',
							'message': 'آیتم به صورت نرم حذف شد و قابل بازیابی است.',
							'deletion_type': 'soft',
							'timestamp': timezone.now().isoformat(),
						},
						status=status.HTTP_200_OK
					)
				else:
					# مدل soft delete را پشتیبانی نمی‌کند
					return Response(
						{
							'status': 'error',
							'message': 'این مدل از حذف نرم پشتیبانی نمی‌کند. برای حذف دائمی از پارامتر permanent=true استفاده کنید.',
							'code': 'SOFT_DELETE_NOT_SUPPORTED',
						},
						status=status.HTTP_400_BAD_REQUEST
					)
					
		except ProtectedError as e:
			# خطای محافظت شده (on_delete=PROTECT)
			protected_objects = []
			for obj in e.protected_objects:
				protected_objects.append({
					'model': obj.__class__.__name__,
					'id': obj.pk,
					'display': str(obj),
				})
			
			return Response(
				{
					'status': 'error',
					'message': 'این آیتم قابل حذف نیست زیرا آیتم‌های دیگری به آن وابسته هستند.',
					'protected_objects': protected_objects,
					'code': 'PROTECTED_ERROR',
				},
				status=status.HTTP_400_BAD_REQUEST
			)
		except Exception as e:
			logger.error(f'خطا در حذف آیتم: {e}', exc_info=True)
			return Response(
				{
					'status': 'error',
					'message': 'خطا در حذف آیتم.',
					'code': 'DELETE_ERROR',
					'details': str(e) if settings.DEBUG else None,
				},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR
			)


def _get_on_delete_behavior(field):
	"""
	استخراج رفتار on_delete از یک ForeignKey
	
	Args:
		field: فیلد ForeignKey یا OneToOneField
		
	Returns:
		str: رفتار on_delete
	"""
	
	on_delete = field.remote_field.on_delete
	
	if on_delete == CASCADE:
		return 'CASCADE'
	elif on_delete == PROTECT:
		return 'PROTECT'
	elif on_delete == SET_NULL:
		return 'SET_NULL'
	elif on_delete == SET_DEFAULT:
		return 'SET_DEFAULT'
	elif on_delete == DO_NOTHING:
		return 'DO_NOTHING'
	elif on_delete == RESTRICT:
		return 'RESTRICT'
	elif callable(on_delete):
		return 'SET (custom function)'
	else:
		return 'UNKNOWN'
