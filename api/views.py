import json
import math
import inspect
from django.db import models
from django.db.models import Q
from django.shortcuts import render
from django.http import JsonResponse
from django.core.exceptions import FieldDoesNotExist, FieldError, ObjectDoesNotExist, ValidationError  # import for detail_update
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from .registry import model_registry, ModelNotRegistered
from .permissions import restrict_api_by_role
from django.db import IntegrityError



DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100

ALLOWED_FILTER_LOOKUPS = {
	"exact",
	"icontains",
	"contains",
	"gte",
	"lte",
	"gt",
	"lt",
	"in",
}


# =========================================================
# Core helpers
# =========================================================

def _error(code, message, status=400):
	return JsonResponse(
		{"ok": False, "error": {"code": code, "message": message}},
		status=status,
		json_dumps_params={"ensure_ascii": False},
	)
 
 
def _handle_integrity_error(e):
    msg = str(e)
    if "UNIQUE constraint failed:" in msg:
        field = msg.split(".")[-1].strip()
        return _error("unique_violation", f"مقدار {field} قبلاً ثبت شده است", 400)
    return _error("integrity_error", "تعارض داده‌ای رخ داده است", 400)


def _audit(request, action, obj, changes=None):
	"""Record API mutations with actor, IP and device context."""
	try:
		from profiles.models import user_activity_log
		user = getattr(request, 'user', None)
		if not user or not getattr(user, 'is_authenticated', False):
			return
		user_activity_log.objects.create(
			user=user, action=action, model_name=obj._meta.model_name,
			object_id=obj.pk if isinstance(obj.pk, int) else None,
			object_repr=str(obj)[:200], changes=changes or None,
			ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
				or request.META.get('REMOTE_ADDR') or None),
			user_agent=request.META.get('HTTP_USER_AGENT', ''), status='success',
		)
	except Exception:
		# Audit storage must not turn a successful business write into a 500.
		return


def _parse_json(request):
	content_type = request.content_type or ""
	if content_type.startswith("multipart/form-data"):
		data = request.POST.dict()
		for key, uploaded_file in request.FILES.items():
			data[key] = uploaded_file
		return data

	try:
		return json.loads(request.body or "{}")
	except json.JSONDecodeError:
		return None

def _get_model_field_by_path(model, field_path):
	"""
	از روی pathهایی مثل:
	- "id"
	- "user__email"
	- "profile__city__id"

	فیلد نهایی Django model field را پیدا می‌کند.
	اگر مسیر معتبر نباشد None برمی‌گرداند.
	"""

	current_model = model
	model_field = None

	for part in field_path.split("__"):
		try:
			model_field = current_model._meta.get_field(part)
		except:
			return None

		related_model = getattr(model_field, "related_model", None)
		if related_model:
			current_model = related_model

	return model_field

def _coerce_lookup_value(model, lookup_field, raw_value):
	field = _get_model_field_by_path(model, lookup_field)

	if field is None:
		return raw_value

	internal_type = field.get_internal_type()

	if internal_type in {
		"AutoField",
		"BigAutoField",
		"IntegerField",
		"BigIntegerField",
		"SmallIntegerField",
		"PositiveIntegerField",
		"PositiveSmallIntegerField",
	}:
		try:
			return int(raw_value)
		except Exception:
			return raw_value

	if internal_type in {
		"FloatField",
		"DecimalField",
	}:
		try:
			return float(raw_value)
		except Exception:
			return raw_value

	if internal_type == "BooleanField":
		val = str(raw_value).lower().strip()
		if val in {"1", "true", "yes"}:
			return True
		if val in {"0", "false", "no"}:
			return False

	return raw_value


# =========================================================
# HELP ENDPOINTS (ROLE AWARE)
# =========================================================

@require_GET
@restrict_api_by_role()
def model_help(request, model_name):

	config = request.api_model_config
	access = request.api_access
	model = config.model

	readable = access["readable_fields"]
	writable = access["writable_fields"]
	callable_methods = access["callable_methods"]

	return JsonResponse(
		{
			"ok": True,
			"data": {
				"name": config.name,
				"model": {
					"app_label": model._meta.app_label,
					"model_name": model._meta.model_name,
					"object_name": model._meta.object_name,
					"db_table": model._meta.db_table,
					"verbose_name": str(model._meta.verbose_name),
					"verbose_name_plural": str(model._meta.verbose_name_plural),
				},
				"lookup_field": config.lookup_field,
				"soft_delete": {
					"enabled": config.has_soft_delete(),
					"field": config.soft_delete_field,
				},
				"permissions": access["global_permissions"],
				"fields": {
					"readable": _build_field_help(model, readable, writable),
					"writable": _build_writable_field_help(model, writable),
				},
				"methods": {
					"callable": _build_callable_methods_help(model, callable_methods)
				},
				"ordering": sorted(config.get_ordering_fields()),
				"endpoints": {
					"model_help": f"/api/v1/{config.name}/help/",
					"object_help": f"/api/v1/{config.name}/<lookup>/help/",
					"model_collection": f"/api/v1/{config.name}/",
					"model_detail": f"/api/v1/{config.name}/<lookup>/",
					"call_model_method": f"/api/v1/{config.name}/<lookup>/call/<method_name>/",
				},
				"http_methods": {
					"model_help": ["GET"],
					"object_help": ["GET"],
					"model_collection": ["GET", "POST"],
					"model_detail": ["GET", "POST", "DELETE"],
					"call_model_method": ["POST"],
				},
			},
		},
		json_dumps_params={"ensure_ascii": False},
	)


@require_GET
@restrict_api_by_role()
def object_help(request, model_name, lookup):

	config = request.api_model_config
	access = request.api_access
	model = config.model

	lookup_value = _coerce_lookup_value(model, config.lookup_field, lookup)

	try:
		obj = model.objects.get(**{config.lookup_field: lookup_value})
	except model.DoesNotExist:
		return _error("object_not_found", "Object not found", 404)

	if hasattr(model, "api_single_queryset"):
		obj = model.api_single_queryset(request, obj, access)
	if obj is False or obj is None:
		return _error("permission_denied", "You do not have access to this record.", 403)

	readable = access["readable_fields"]
	writable = access["writable_fields"]

	return JsonResponse(
		{
			"ok": True,
			"data": {
				"name": config.name,
				"object": {
					"lookup_field": config.lookup_field,
					"lookup_value": lookup_value,
					"pk": obj.pk,
					"display": str(obj),
					"data": _build_object_snapshot(obj, readable),
				},
				"soft_delete": {
					"enabled": config.has_soft_delete(),
					"field": config.soft_delete_field,
					"current_value": _get_soft_delete_value(obj, config),
				},
				"permissions": access["global_permissions"],
				"fields": {
					"readable": _build_field_help(model, readable, writable),
					"writable": _build_writable_field_help(model, writable),
				},
				"methods": {
					"callable": _build_callable_methods_help(
						model, access["callable_methods"]
					)
				},
			},
		},
		json_dumps_params={"ensure_ascii": False},
	)

def _build_collection_query_docs(config):
	search_fields = list(config.search_fields or [])
	ordering_fields = sorted(config.get_ordering_fields())
	readable_fields = list(config.readable_fields or [])
	lookup_examples = sorted(ALLOWED_FILTER_LOOKUPS)

	example_filters = []
	if readable_fields:
		first_field = readable_fields[0]
		example_filters.append(
			{
				"name": first_field,
				"example": f"{first_field}=sample",
				"description": "فیلتر exact روی فیلد مستقیم",
			}
		)
		example_filters.append(
			{
				"name": f"{first_field}__icontains",
				"example": f"{first_field}__icontains=test",
				"description": "جستجوی شامل روی فیلد متنی",
			}
		)
		example_filters.append(
			{
				"name": f"{first_field}__in",
				"example": f"{first_field}__in=1,2,3",
				"description": "چند مقدار با جداکننده comma",
			}
		)

	return {
		"query_params": [
			{
				"name": "search",
				"type": "string",
				"default": "",
				"supported": bool(search_fields),
				"description": "جستجوی متنی روی search_fields تنظیم‌شده مدل",
				"example": f"search=test" if search_fields else None,
			},
			{
				"name": "perpage",
				"type": "integer",
				"default": DEFAULT_PER_PAGE,
				"supported": True,
				"description": f"تعداد نتایج هر صفحه. حداکثر {MAX_PER_PAGE}",
				"example": "perpage=20",
			},
			{
				"name": "pagenumber",
				"type": "integer",
				"default": 1,
				"supported": True,
				"description": "شماره صفحه. اگر بیشتر از total_pages باشد به آخرین صفحه clamp می‌شود",
				"example": "pagenumber=2",
			},
			{
				"name": "sort",
				"type": "string",
				"default": "",
				"supported": bool(ordering_fields),
				"description": "مرتب‌سازی با comma-separated fields. برای نزولی از - استفاده کنید",
				"example": f"sort={ordering_fields[0]}" if ordering_fields else None,
			},
			{
				"name": "fields",
				"type": "string",
				"default": "all readable fields",
				"supported": bool(readable_fields),
				"description": "انتخاب subset از فیلدهای readable برای خروجی",
				"example": f"fields={','.join(readable_fields[:2])}" if readable_fields else None,
			},
			{
				"name": "<field>[__lookup]",
				"type": "dynamic",
				"default": "",
				"supported": True,
				"description": "فیلتر پویا روی فیلدها و relation pathها",
				"example": example_filters[0]["example"] if example_filters else None,
			},
		],
		"search_fields": search_fields,
		"ordering_fields": ordering_fields,
		"readable_fields": readable_fields,
		"allowed_filter_lookups": lookup_examples,
		"filter_examples": example_filters,
	}

def _build_collection_examples(config):
	base = f"/api/v1/{config.name}/"

	readable_fields = list(config.readable_fields or [])
	ordering_fields = sorted(config.get_ordering_fields())
	search_fields = list(config.search_fields or [])

	fields_example = ",".join(readable_fields[:2]) if readable_fields else ""
	sort_example = ordering_fields[0] if ordering_fields else ""
	search_example = "test" if search_fields else ""

	examples = [
		{
			"title": "لیست ساده",
			"request": f"GET {base}",
			"description": "دریافت لیست رکوردها با تنظیمات پیش‌فرض صفحه‌بندی",
		},
		{
			"title": "صفحه‌بندی",
			"request": f"GET {base}?perpage=20&pagenumber=2",
			"description": "دریافت صفحه دوم با 20 رکورد در هر صفحه",
		},
	]

	if search_fields:
		examples.append(
			{
				"title": "جستجو",
				"request": f"GET {base}?search={search_example}",
				"description": f"جستجو روی فیلدهای: {', '.join(search_fields)}",
			}
		)

	if sort_example:
		examples.append(
			{
				"title": "مرتب‌سازی",
				"request": f"GET {base}?sort=-{sort_example}",
				"description": f"مرتب‌سازی نزولی بر اساس {sort_example}",
			}
		)

	if fields_example:
		examples.append(
			{
				"title": "انتخاب فیلدها",
				"request": f"GET {base}?fields={fields_example}",
				"description": "فقط همان فیلدهای انتخاب‌شده در results برگردانده می‌شوند",
			}
		)

	examples.append(
		{
			"title": "فیلتر exact",
			"request": f"GET {base}?id=1",
			"description": "فیلتر مستقیم exact روی یک فیلد",
		}
	)

	examples.append(
		{
			"title": "فیلتر relation-aware",
			"request": f"GET {base}?user__email__icontains=ali",
			"description": "فیلتر روی relation path با lookup",
		}
	)

	examples.append(
		{
			"title": "فیلتر in",
			"request": f"GET {base}?category__id__in=1,2,3",
			"description": "فیلتر چندمقداری با lookup=in",
		}
	)

	complex_query_parts = ["perpage=10", "pagenumber=1"]

	if search_fields:
		complex_query_parts.append("search=test")

	if sort_example:
		complex_query_parts.append(f"sort=-{sort_example}")

	if fields_example:
		complex_query_parts.append(f"fields={fields_example}")

	complex_query_parts.append("user__email__icontains=ali")

	examples.append(
		{
			"title": "مثال ترکیبی کامل",
			"request": f"GET {base}?{'&'.join(complex_query_parts)}",
			"description": "نمونه کامل با search، pagination، sort، fields و filter همزمان",
		}
	)

	return examples

def _build_collection_error_docs(config):
	errors = [
		{
			"code": "permission_denied",
			"status": 403,
			"description": "کاربر مجوز view ندارد",
		},
		{
			"code": "invalid_filter",
			"status": 400,
			"description": "کلید فیلتر نامعتبر است یا root field مجاز نیست",
		},
		{
			"code": "invalid_lookup",
			"status": 400,
			"description": "lookup استفاده‌شده در فهرست lookupهای مجاز نیست",
		},
		{
			"code": "invalid_pagination",
			"status": 400,
			"description": "مقدار perpage یا pagenumber نامعتبر است",
		},
		{
			"code": "invalid_fields",
			"status": 400,
			"description": "فیلدهای خواسته‌شده در پارامتر fields جزو readable_fields نیستند",
		},
	]

	if config.search_fields:
		errors.append(
			{
				"code": "search_not_supported",
				"status": 400,
				"description": "search برای این مدل پیکربندی نشده یا فیلدهای search قابل کوئری نیستند",
			}
		)

	if config.get_ordering_fields():
		errors.append(
			{
				"code": "invalid_sort",
				"status": 400,
				"description": "فیلد مرتب‌سازی جزو ordering_fields مجاز نیست",
			}
		)

	return errors

def _build_method_docs(model, method_names, model_name):
	result = []

	for method_name in sorted(method_names):
		attr = getattr(model, method_name, None)

		signature_text = None
		parameters = []

		if callable(attr):
			try:
				sig = inspect.signature(attr)
				signature_text = str(sig)

				for param_name, param in sig.parameters.items():
					parameters.append(
						{
							"name": param_name,
							"kind": str(param.kind),
							"required": param.default is inspect._empty,
							"default": None if param.default is inspect._empty else repr(param.default),
						}
					)
			except Exception:
				signature_text = "()"

		example_payload_dict = {
			param["name"]: "value"
			for param in parameters
			if param["name"] != "self"
		}

		result.append(
			{
				"name": method_name,
				"doc": _clean_docstring(getattr(attr, "__doc__", "") or ""),
				"signature": signature_text or "()",
				"parameters": parameters,
				"endpoint": f"/api/v1/{model_name}/<lookup>/call/{method_name}/",
				"example_payload": json.dumps(
					example_payload_dict,
					ensure_ascii=False,
					indent=2,
				),
				"example_request": f"POST /api/v1/{model_name}/<lookup>/call/{method_name}/",
			}
		)

	return result


def _build_model_api_doc(config):
	model = config.model

	method_docs = _build_method_docs(model, config.callable_methods, config.name)
	collection_docs = _build_collection_query_docs(config)
	collection_examples = _build_collection_examples(config)
	collection_errors = _build_collection_error_docs(config)

	return {
		"name": config.name,
		"model": {
			"app_label": model._meta.app_label,
			"model_name": model._meta.model_name,
			"verbose_name": str(model._meta.verbose_name),
			"verbose_name_plural": str(model._meta.verbose_name_plural),
		},
		"lookup_field": config.lookup_field,
		"soft_delete": {
			"enabled": config.has_soft_delete(),
			"field": config.soft_delete_field,
		},
		"fields": {
			"readable": config.readable_fields,
			"writable": config.writable_fields,
		},
		"methods": {
			"callable": config.callable_methods,
			"docs": method_docs,
		},
		"ordering": sorted(config.get_ordering_fields()),
		"search_fields": list(config.search_fields or []),
		"filter_lookups": sorted(ALLOWED_FILTER_LOOKUPS),
		"endpoints": {
			"model_help": f"/api/v1/{config.name}/help/",
			"object_help": f"/api/v1/{config.name}/<lookup>/help/",
			"model_collection": f"/api/v1/{config.name}/",
			"model_detail": f"/api/v1/{config.name}/<lookup>/",
			"call_model_method": f"/api/v1/{config.name}/<lookup>/call/<method_name>/",
		},
		"collection_docs": collection_docs,
		"collection_examples": collection_examples,
		"collection_errors": collection_errors,
	}

@require_GET
def api_help(request):
	"""
	صفحه مستندات کامل API
	تمام model‌ها، endpoint‌ها و نحوه استفاده را نمایش می‌دهد
	"""

	registered_models = []

	for model_name, config in model_registry.all().items():
		registered_models.append(_build_model_api_doc(config))

	registered_models.sort(key=lambda x: x["name"])

	auth_info = {
		"endpoints": [
			{
				"name": "بررسی شماره موبایل",
				"path": "/api/v1/auth/check-phone/",
				"method": "POST",
				"auth_required": False,
				"description": "اولین قدم در فرآیند ورود/ثبت‌نام"
			},
			{
				"name": "درخواست کد تایید",
				"path": "/api/v1/auth/request-otp/",
				"method": "POST",
				"auth_required": False,
				"description": "ارسال OTP برای کاربران موجود"
			},
			{
				"name": "ورود با رمز عبور",
				"path": "/api/v1/auth/login-password/",
				"method": "POST",
				"auth_required": False,
				"description": "ورود با شماره و رمز عبور"
			},
			{
				"name": "تایید کد OTP",
				"path": "/api/v1/auth/verify-otp/",
				"method": "POST",
				"auth_required": False,
				"description": "تایید کد و ورود/ثبت‌نام"
			},
			{
				"name": "خروج از حساب",
				"path": "/api/v1/auth/logout/",
				"method": "POST",
				"auth_required": True,
				"description": "خروج و غیرفعال‌سازی نشست"
			},
		],
		"method": "Bearer Token",
		"header": "Authorization: Bearer <session_key>",
		"flow": [
			"بررسی شماره موبایل با check-phone",
			"بر اساس next_step: ورود با رمز یا OTP",
			"دریافت session_key",
			"استفاده در هدر Authorization",
		]
	}

	api_info = {
		"title": "مستندات API",
		"version": "1.0",
		"base_url": "/api/v1",
		"description": "این API بر اساس معماری مدل‌محور طراحی شده و تمام عملیات CRUD، جستجو، فیلتر، مرتب‌سازی، صفحه‌بندی، انتخاب فیلدها و فراخوانی متدها را از طریق endpoint‌های استانداردشده ارائه می‌دهد.",
		"collection_features": [
			"فیلتر پویا روی فیلدها و relation pathها",
			"جستجو با search روی search_fields",
			"مرتب‌سازی با sort",
			"صفحه‌بندی با perpage و pagenumber",
			"انتخاب فیلدهای خروجی با fields",
			"lookupهای مجاز: " + ", ".join(sorted(ALLOWED_FILTER_LOOKUPS)),
		],
		"error_response_example": {
			"ok": False,
			"error": {
				"code": "error_code",
				"message": "پیام خطا",
			},
		},
	}

	context = {
		"api_info": api_info,
		"auth_info": auth_info,
		"models": registered_models,
		"total_models": len(registered_models),
	}

	return render(request, "api/api_help.html", context)

# =========================================================
# COLLECTION
# =========================================================

@require_http_methods(["GET", "POST"])
@restrict_api_by_role()
def model_collection(request, model_name):

	config = request.api_model_config
	access = request.api_access
	# print("accessssssssssssssssssss:")
	# print(access)
	# print("accessssssssssssssssssss:")
	if request.method == "GET":
		return _collection_list(request, config, access)

	return _collection_create(request, config, access)

def _parse_filter_key(raw_key):
	"""
	تجزیه کلید فیلتر querystring.

	نمونه‌ها:
	- "name" -> ("name", "exact")
	- "age__gte" -> ("age", "gte")
	- "user__email__icontains" -> ("user__email", "icontains")
	- "profile__city__name" -> ("profile__city__name", "exact")
	"""

	parts = [part.strip() for part in str(raw_key).split("__") if part.strip()]

	if not parts:
		return None, None

	if parts[-1] in ALLOWED_FILTER_LOOKUPS:
		lookup = parts[-1]
		field_path = "__".join(parts[:-1])

		if not field_path:
			return None, None

		return field_path, lookup

	return "__".join(parts), "exact"

def _collection_list(request, config, access):
	if not access["global_permissions"]["view"]:
		return _error("permission_denied", "Retrieve not allowed", 403)

	model = config.model
	readable_fields = set(access["readable_fields"])
	ordering_fields = set(config.get_ordering_fields())
	search_fields = set(config.search_fields or [])

	queryset = model.objects.all()
	if hasattr(model, "api_collection_queryset"):
		queryset = model.api_collection_queryset(request, queryset, access)
	# soft delete

	if config.has_soft_delete():
		queryset = queryset.filter(**{config.soft_delete_field: False})

	# -----------------------------------------------------
	# filters
	# -----------------------------------------------------
	filters = {}

	for key, value in request.GET.items():
		if key in {"search", "perpage", "pagenumber", "sort", "fields"}:
			continue

		field_path, lookup = _parse_filter_key(key)

		if not field_path:
			return _error("invalid_filter", f"Invalid filter key: {key}", 400)

		# فقط root field را برای whitelist چک می‌کنیم
		root_field = field_path.split("__", 1)[0]

		if root_field not in readable_fields:
			return _error("invalid_filter", f"{root_field} not filterable")

		if lookup not in ALLOWED_FILTER_LOOKUPS:
			return _error("invalid_lookup", lookup)

		if lookup == "in":
			raw_items = [item.strip() for item in str(value).split(",") if item.strip()]
			value = [_coerce_lookup_value(model, field_path, item) for item in raw_items]
		else:
			value = _coerce_lookup_value(model, field_path, value)

		filters[f"{field_path}__{lookup}" if lookup != "exact" else field_path] = value

	if filters:
		# The whitelist above validates only the ROOT field, so a nested path that does not
		# exist, a reverse relation, or a value the field cannot parse still reaches Django and
		# raises. Unguarded that was a 500 and an HTML page instead of the JSON envelope.
		try:
			queryset = queryset.filter(**filters)
			# filter() builds the query lazily, so a bad lookup or an unparsable value only
			# raises when the SQL is compiled -- which used to happen during pagination, well
			# outside this guard. Compiling here (no database round trip) surfaces it now.
			str(queryset.query)
		except (FieldError, ValidationError, ValueError, TypeError) as exc:
			return _error("invalid_filter", str(exc), 400)

	# -----------------------------------------------------
	# search
	# -----------------------------------------------------
	search_term = (request.GET.get("search") or "").strip()

	if search_term:
		if not search_fields:
			return _error("search_not_supported", "Search is not configured for this model", 400)

		search_query = Q()

		for field_name in search_fields:
			try:
				model._meta.get_field(field_name)
			except models.FieldDoesNotExist:
				# برای search فقط روی fieldهای واقعی دیتابیس کوئری می‌زنیم
				continue

			search_query |= Q(**{f"{field_name}__icontains": search_term})

		if not search_query.children:
			return _error(
				"search_not_supported",
				"No database-backed search fields are configured for this model",
				400,
			)

		queryset = queryset.filter(search_query)

	# -----------------------------------------------------
	# sort
	# -----------------------------------------------------
	sort_param = (request.GET.get("sort") or "").strip()

	if sort_param:
		raw_sort_fields = [item.strip() for item in sort_param.split(",") if item.strip()]
		order_by_fields = []

		for raw_field in raw_sort_fields:
			field_name = raw_field.lstrip("-")

			if field_name not in ordering_fields:
				return _error("invalid_sort", f"{field_name} not sortable", 400)

			order_by_fields.append(raw_field)

		if order_by_fields:
			queryset = queryset.order_by(*order_by_fields)

	# -----------------------------------------------------
	# pagination
	# -----------------------------------------------------
	try:
		perpage = int(request.GET.get("perpage", DEFAULT_PER_PAGE))
		page = int(request.GET.get("pagenumber", 1))
	except ValueError:
		return _error("invalid_pagination", "Invalid pagination")

	if perpage < 1:
		return _error("invalid_pagination", "perpage must be >= 1", 400)

	if page < 1:
		return _error("invalid_pagination", "pagenumber must be >= 1", 400)

	perpage = min(perpage, MAX_PER_PAGE)

	total = queryset.count()
	total_pages = max(1, math.ceil(total / perpage))

	if page > total_pages:
		page = total_pages

	offset = (page - 1) * perpage
	queryset = queryset[offset: offset + perpage]

	# -----------------------------------------------------
	# fields
	# -----------------------------------------------------
	fields_param = (request.GET.get("fields") or "").strip()

	if fields_param:
		requested_fields = {item.strip() for item in fields_param.split(",") if item.strip()}

		if not requested_fields:
			selected_fields = readable_fields
		else:
			invalid_fields = requested_fields - readable_fields
			if invalid_fields:
				return _error(
					"invalid_fields",
					f"These fields are not readable: {sorted(invalid_fields)}",
					400,
				)

			selected_fields = requested_fields
	else:
		selected_fields = readable_fields

	results = [
		_serialize_object(obj, selected_fields, request=request)
		for obj in queryset
	]

	return JsonResponse(
		{
			"ok": True,
			"data": {
				"pagination": {
					"page": page,
					"perpage": perpage,
					"total": total,
					"total_pages": total_pages,
				},
				"filters": {
					"search": search_term or None,
					"sort": sort_param or None,
					"fields": sorted(selected_fields),
				},
				"results": results,
			},
		},
		json_dumps_params={"ensure_ascii": False},
	)

def _collection_create(request, config, access):

	if not access["global_permissions"]["add"]:
		return _error("permission_denied", "Create not allowed", 403)

	payload = _parse_json(request)
	if payload is None:
		return _error("invalid_json", "Invalid JSON")

	writable = set(access["writable_fields"])
	forbidden = set(payload.keys()) - writable
	if forbidden:
		return _error("forbidden_fields", list(forbidden), 403)

	veto = config.model.api_can_write(request, obj=None, payload=payload)
	if veto:
		return _error(veto[0], veto[1], 403)

	prepare_payload = getattr(config.model, "api_prepare_create_payload", None)
	if callable(prepare_payload):
		try:
			payload = prepare_payload(request, dict(payload))
		except ValidationError as exc:
			return _validation_error_response(exc)

	# Resolve FK/O2O fields from raw IDs to model instances before create()
	resolved = {}
	for field, value in payload.items():
		f = _get_model_field(config.model, field)

		if _is_single_relation(f):

			if value is None:
				resolved[field] = None
			else:
				try:
					related = f.remote_field.model.objects.get(pk=value)
				except (ObjectDoesNotExist, ValueError, TypeError):
					return _error(
						"invalid_reference",
						f"{field}: no record with id {value}",
						400,
					)
				resolved[field] = related
		else:
			resolved[field] = value

	try:
		obj = config.model.objects.create(**resolved)
	except ValidationError as exc:
		return _validation_error_response(exc)
	except IntegrityError as exc:
		return _handle_integrity_error(exc)
	_audit(request, 'create', obj, {'fields': sorted(payload.keys())})

	return JsonResponse(
		{
			"ok": True,
			"data": _serialize_object(obj, access["readable_fields"], request=request),
		},
		status=201,
	)

# =========================================================
# DETAIL
# =========================================================

@require_http_methods(["GET", "POST", "DELETE"])
@restrict_api_by_role()
def model_detail(request, model_name, lookup):

	config = request.api_model_config
	access = request.api_access
	model = config.model

	lookup_value = _coerce_lookup_value(model, config.lookup_field, lookup)
	try:
		obj = model.objects.get(**{config.lookup_field: lookup_value})
	except model.DoesNotExist:
		return _error("object_not_found", "Object not found", 404)

	if hasattr(model, "api_single_queryset"):
		obj = model.api_single_queryset(request, obj, access)

	if obj is False or obj is None:
		return _error("permission_denied", "You do not have access to this record.", 403)


	if request.method == "GET":
		return _detail_get(obj, access, request=request)

	if request.method == "POST":
		return _detail_update(request, obj, access)

	return _detail_delete(request, obj, config, access)

def _detail_get(obj, access, request=None):

	if not access["global_permissions"]["view"]:
		return _error("permission_denied", "Retrieve not allowed", 403)

	return JsonResponse(
		{
			"ok": True,
			"data": _serialize_object(obj, access["readable_fields"], request=request),
		}
	)

def _detail_update(request, obj, access):
    # ۱. بررسی دسترسی کلی (Authorization Check)
    if not access["global_permissions"]["change"]:
        return _error("permission_denied", "Update not allowed", 403)

    # ۲. پارس کردن داده‌های ورودی
    payload = _parse_json(request)
    if payload is None:
        return _error("invalid_json", "Invalid JSON")

    # ۳. بررسی Veto (منطق بیزینسی خاص مدل)
    veto = obj.__class__.api_can_write(request, obj=obj, payload=payload)
    if veto:
        return _error(veto[0], veto[1], 403)

    # ۴. فیلتر کردن فیلدهای غیرمجاز (The Core Change)
    writable = set(access["writable_fields"])
    # به جای خطا دادن، فقط فیلدهایی را نگه می‌داریم که در لیست مجاز هستند
    sanitized_payload = {k: v for k, v in payload.items() if k in writable}
    
    # اختیاری: اگر بعد از فیلتر کردن، هیچ فیلدی برای آپدیت باقی نماند
    if not sanitized_payload:
        return JsonResponse({"ok": True, "message": "No writable fields provided", "data": {"updated": False}})

    # ۵. اعمال تغییرات روی آبجکت
    for field, value in sanitized_payload.items():
        f = _get_model_field(obj.__class__, field)

        if _is_single_relation(f):
            if value is None:
                setattr(obj, field, None)
            else:
                # جلوگیری از ارجاع به خود (Self-reference check)
                if f.remote_field.model is obj.__class__ and str(value) == str(obj.pk):
                    return _error("invalid_reference", f"{field}: record cannot reference itself", 400)
                
                try:
                    related = f.remote_field.model.objects.get(pk=value)
                except (ObjectDoesNotExist, ValueError, TypeError):
                    return _error("invalid_reference", f"{field}: no record with id {value}", 400)
                
                setattr(obj, field, related)
        else:
            # اعمال مستقیم مقدار برای فیلدهای غیر رابطه‌ای
            setattr(obj, field, value)

    # ۶. ذخیره‌سازی و مدیریت خطاهای پایگاه داده
    try:
        obj.save()
    except IntegrityError as e:
        return _handle_integrity_error(e)

    return JsonResponse({"ok": True, "data": {"updated": True}})

def _detail_delete(request, obj, config, access):

	if not access["global_permissions"]["delete"]:
		return _error("permission_denied", "Delete not allowed", 403)

	veto = obj.__class__.api_can_write(request, obj=obj, payload={"_delete": True})
	if veto:
		return _error(veto[0], veto[1], 403)

	if config.has_soft_delete():

		setattr(obj, config.soft_delete_field, True)
		obj.save()
		_audit(request, 'delete', obj, {'type': 'soft'})

		return JsonResponse(
			{"ok": True, "data": {"deleted": True, "type": "soft"}}
		)

	_audit(request, 'delete', obj, {'type': 'hard'})
	obj.delete()

	return JsonResponse(
		{"ok": True, "data": {"deleted": True, "type": "hard"}}
	)


# =========================================================
# CALL MODEL METHOD
# =========================================================

@require_POST
@restrict_api_by_role(force_action="call")
def call_model_method(request, model_name, lookup, method_name):

	config = request.api_model_config
	access = request.api_access
	model = config.model
	if method_name not in access["callable_methods"]:
		return _error("method_not_allowed", method_name, 403)

	lookup_value = _coerce_lookup_value(model, config.lookup_field, lookup)

	try:
		obj = model.objects.get(**{config.lookup_field: lookup_value})
	except model.DoesNotExist:
		return _error("object_not_found", "Object not found", 404)

	if hasattr(model, "api_single_queryset"):
		obj = model.api_single_queryset(request, obj, access)
	if obj is False or obj is None:
		return _error("permission_denied", "You do not have access to this record.", 403)

	method = getattr(obj, method_name)

	payload = _parse_json(request)

	if payload is None:
		return _error("invalid_json", "Invalid JSON")

	try:
		result = _invoke_method(method, payload, actor=request.user)
	except TypeError as exc:
		return _error("invalid_arguments", str(exc))
	except ValidationError as exc:
		return _validation_error_response(exc)
	except Exception as exc:
		return _error("method_execution_failed", str(exc), 500)
	_audit(request, f'call:{method_name}', obj, {'arguments': sorted(payload.keys())})

	return JsonResponse({"ok": True, "data": _serialize_value(result)})

def _invoke_method(method, payload, actor=None):

	signature = inspect.signature(method)

	if not signature.parameters:
		return method()

	arguments = dict(payload)
	if "actor" in signature.parameters:
		# The authenticated request is the actor. Never trust a caller-provided id
		# for audit history or authorization decisions.
		arguments["actor"] = actor

	return method(**arguments)


def _validation_error_response(exc):
	if hasattr(exc, "message_dict"):
		message = exc.message_dict
	elif hasattr(exc, "messages"):
		message = exc.messages
	else:
		message = str(exc)
	return _error("validation_error", message, 400)

# =========================================================
# SERIALIZATION
# =========================================================

def _is_single_relation(field):
	return (
		field is not None
		and field.is_relation
		and (field.many_to_one or field.one_to_one)
	)


def _is_multi_relation(field):
	return field is not None and field.is_relation and (field.many_to_many or field.one_to_many)

def _get_model_field(model, field_name):
	try:
		return model._meta.get_field(field_name)
	except :
		return None

def _serialize_relation(value):
	if value is None:
		return None

	return {
		"id": value.pk,
		"model": value.__class__._meta.model_name,
		"display": str(value),
	}

def _get_relation_choices(field):
    if not _is_single_relation(field):
        return None

    related_model = field.remote_field.model
    queryset = related_model.objects.all()

    # Unfiltered .all() offered soft-deleted rows as valid choices -- a trashed
    # ticket_department (or panel_menu parent, or any other self-referential/FK choices
    # list) still showed up here with its real name, even though the list endpoint for
    # that same model correctly excludes it via config.soft_delete_field. Selecting one
    # then failed with a validation error the picker gave no way to predict. Mirrors
    # ModelApiConfig.has_soft_delete()'s own field-existence check, applied to the related
    # model directly since choices are built from the Django model class, not a registry
    # config.
    soft_delete_field = getattr(related_model, 'api_soft_delete_field', lambda: None)()
    if soft_delete_field:
        try:
            related_model._meta.get_field(soft_delete_field)
            queryset = queryset.filter(**{soft_delete_field: False})
        except FieldDoesNotExist:
            pass

    return [
        {
            "value": obj.pk,
            "label": obj.__str__() or str(obj.pk),
        }
        for obj in queryset
    ]

def _serialize_field_value(obj, field_name):
	field = _get_model_field(obj.__class__, field_name)

	try:
		value = getattr(obj, field_name)

		if callable(value):
			value = value()

	except Exception:
		return None

	if _is_single_relation(field):
		return _serialize_relation(value)
	if _is_multi_relation(field):
		return [_serialize_relation(item) for item in value.all()]

	return _serialize_value(value)

def _serialize_object(obj, readable_fields, request=None):
	result = {}
	hidden = set()
	if request is not None:
		hidden_hook = getattr(obj.__class__, 'api_hidden_fields', None)
		if callable(hidden_hook):
			hidden = set(hidden_hook(request, obj) or [])

	for field_name in readable_fields:
		if field_name in hidden:
			continue
		result[field_name] = _serialize_field_value(obj, field_name)

	return result

def _serialize_value(value):

	if value is None:
		return None

	if isinstance(value, (str, int, float, bool, dict, list)):
		return value

	return str(value)

# =========================================================
# INTROSPECTION HELPERS
# =========================================================

def _build_object_snapshot(obj, readable_fields):
	result = {}
	for field_name in readable_fields:
		try:
			value = getattr(obj, field_name)

			if callable(value):
				value = value()

		except Exception:
			value = None

		field = _get_model_field(obj.__class__, field_name)
		if _is_single_relation(field):
			result[field_name] = _serialize_relation(value)
		elif _is_multi_relation(field):
			result[field_name] = [_serialize_relation(item) for item in value.all()]
		else:
			result[field_name] = _serialize_value(value)

	return result

def _get_soft_delete_value(obj, config):
	if not config.has_soft_delete():
		return None
	try:
		return bool(getattr(obj, config.soft_delete_field))
	except Exception:
		return None

def _build_field_help(model, readable_fields, writable_fields):
	result = []
	for field_name in sorted(readable_fields):

		item = {
			"name": field_name,
			"readable": True,
			"writable": field_name in writable_fields,
		}

		try:
			model_field = model._meta.get_field(field_name)
			item.update(_describe_model_field(model_field))
			item["source"] = "model_field"

		except:

			attr = getattr(model, field_name, None)

			item.update(
				{
					"type": _describe_python_attr_type(attr),
					"required": False,
					"nullable": True,
					"default": None,
					"help_text": "",
					"choices": [],
				}
			)

			if isinstance(attr, property):
				item["source"] = "property"
			elif callable(attr):
				item["source"] = "method"
			else:
				item["source"] = "attribute"

		result.append(item)

	return result

def _build_writable_field_help(model, writable_fields):
	result = []
	for field_name in writable_fields:
		field = _get_model_field(model, field_name)

		item = {
			"name": field_name,
			"type": field.get_internal_type() if field else None,
			"required": bool(field and not field.blank and not field.null),
			"nullable": bool(field and field.null),
		}

		# verbose_name / help_text / default / choices, exactly as the readable half
		# reports them. Without this every create and edit form shows the raw field name.
		if field is not None:
			described = _describe_model_field(field)
			item["verbose_name"] = described["verbose_name"]
			item["help_text"] = described["help_text"]
			item["default"] = described["default"]
			if described["choices"]:
				item["choices"] = described["choices"]

		if _is_single_relation(field):
			item.update(
				{
					"type": "ForeignKey"
					if field.many_to_one
					else "OneToOneField",
					"related_model": {
						"app_label": field.remote_field.model._meta.app_label,
						"model_name": field.remote_field.model._meta.model_name,
						"object_name": field.remote_field.model._meta.object_name,
					},
					"choices": _get_relation_choices(field),
				}
			)
		elif "choices" not in item:
			# only when the describer above found none; it must not be overwritten
			item["choices"] = None

		result.append(item)

	return result

def _build_callable_methods_help(model, callable_methods):
	result = []
	for method_name in sorted(callable_methods):

		attr = getattr(model, method_name, None)

		result.append(
			{
				"name": method_name,
				"callable": callable(attr),
				"doc": _clean_docstring(getattr(attr, "__doc__", "") or ""),
			}
		)

	return result

def _describe_model_field(model_field):
	default = model_field.default
	if default is models.NOT_PROVIDED:
		default = None
	elif callable(default):
		default = "<callable>"

	choices = []

	if getattr(model_field, "choices", None):

		choices = [
			{"value": value, "label": str(label)}
			for value, label in model_field.choices
		]

	return {
		"name": model_field.name,
		"type": model_field.get_internal_type(),
		"required": not model_field.blank and not model_field.null,
		"nullable": model_field.null,
		"default": default,
		"help_text": str(model_field.help_text or ""),
		"verbose_name": str(model_field.verbose_name or model_field.name),
		"choices": choices,
	}

def _describe_python_attr_type(attr):

	if isinstance(attr, property):
		return "property"

	if callable(attr):
		return "method"

	if attr is None:
		return "attribute"

	return type(attr).__name__

def _clean_docstring(docstring):

	if not docstring:
		return ""

	return " ".join(
		line.strip()
		for line in docstring.strip().splitlines()
		if line.strip()
	)
