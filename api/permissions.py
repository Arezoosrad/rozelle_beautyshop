# api/permissions.py

from functools import wraps
from django.http import JsonResponse
from django.utils.functional import SimpleLazyObject

from .registry import model_registry, ModelNotRegistered


HTTP_ACTION_MAP = {
	"GET": "read",
	"POST": "write",
	"PUT": "write",
	"PATCH": "write",
	"DELETE": "delete",
}


# ---------------------------------------------------------
# Model Identity
# ---------------------------------------------------------

def _get_model_identity(model):
	return model._meta.app_label, model._meta.model_name


# ---------------------------------------------------------
# Permission Codename Builder
# ---------------------------------------------------------

def _build_permission_codename(app_label, model_name, action, field_name=None, method_name=None):
    """
    منبع واحد حقیقت برای فرمت codename مجوزها.
      سطح مدل : app_label.action_modelname
      سطح فیلد: app_label.action_modelname.field_name
      سطح متد : app_label.call_modelname.method_name   (اکشن همیشه call)
    """
    if method_name is not None:
        # متدها همیشه با اکشن call ثبت می‌شوند، مستقل از action ورودی
        return f"{app_label}.call_{model_name}.{method_name}"

    base = f"{app_label}.{action}_{model_name}"
    if field_name is not None:
        return f"{base}.{field_name}"
    return base



# ---------------------------------------------------------
# Anonymous Role
# ---------------------------------------------------------

_ANONYMOUS_ROLE_CACHE = None


def _get_anonymous_role():
	"""
	Cached lookup for anonymous role.
	Prevents repeated DB queries.
	"""
	global _ANONYMOUS_ROLE_CACHE

	if _ANONYMOUS_ROLE_CACHE is not None:
		return _ANONYMOUS_ROLE_CACHE

	from profiles.models import user_role

	try:
		_ANONYMOUS_ROLE_CACHE = user_role.objects.get(name="anonymous")
	except user_role.DoesNotExist:
		_ANONYMOUS_ROLE_CACHE = None

	return _ANONYMOUS_ROLE_CACHE


# ---------------------------------------------------------
# Role Resolver
# ---------------------------------------------------------

def _resolve_user_role(user):

	if getattr(user, "is_authenticated", False):
		return getattr(user, "role", None)

	return _get_anonymous_role()


# ---------------------------------------------------------
# Permission Preloader
# ---------------------------------------------------------

def _load_role_permissions(role):
	"""
	Load all permission codenames for role in a single query.
	Returns a Python set for O(1) membership checks.
	فقط مجوزهایی که is_granted=True هستند بارگذاری می‌شوند.
	"""

	if not role:
		return set()

	if hasattr(role, "_perm_cache"):
		return role._perm_cache

	# بارگذاری فقط مجوزهایی که در role_permission با is_granted=True هستند
	from profiles.models import role_permission
	
	perms = role_permission.objects.filter(
		role=role,
		is_granted=True,
		permission__is_active=True
	).select_related('permission').values_list(
		'permission__codename',
		flat=True
	)
	
	perm_set = set(perms)
	role._perm_cache = perm_set
	return perm_set


# ---------------------------------------------------------
# Permission Checker
# ---------------------------------------------------------

def _user_has_permission(user, perm_set, codename):

	if getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
		return True

	# user_profile.has_perm_custom is the source of truth for the effective
	# permission: an active per-user deny wins over a role grant and an active
	# per-user allow can supplement the role.  The old resolver only inspected
	# role_permission, so the override controls shown in the panel had no effect
	# on actual API access.
	if getattr(user, "is_authenticated", False):
		checker = getattr(user, "has_perm_custom", None)
		if callable(checker):
			return checker(codename)

	return codename in perm_set


def _user_permission_override(user, codename):
	"""Return an active explicit user override, or None when no override exists."""
	if not getattr(user, "is_authenticated", False):
		return None

	overrides = getattr(user, "user_permissions_custom", None)
	if overrides is None:
		return None

	from django.db.models import Q
	from django.utils import timezone

	row = overrides.filter(
		permission__codename=codename,
		permission__is_active=True,
	).filter(
		Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
	).values_list("is_granted", flat=True).first()
	return row if row is not None else None


# ---------------------------------------------------------
# Access Resolver
# ---------------------------------------------------------

def _resolve_access(user, config):

	role = _resolve_user_role(user)
	perm_set = _load_role_permissions(role)
	app_label, model_name = _get_model_identity(config.model)
	def has_perm(action, field=None, method=None):
		codename = _build_permission_codename(
			app_label,
			model_name,
			action,
			field_name=field,
			method_name=method,
		)
		return _user_has_permission(user, perm_set, codename)

	# -----------------------------------------------------
	# Global permissions
	# -----------------------------------------------------
	global_view = has_perm("view")
	global_add = has_perm("add")
	global_change = has_perm("change")
	global_delete = has_perm("delete")
	global_call = has_perm("call")

	# -----------------------------------------------------
	# Field permissions
	# -----------------------------------------------------

	readable_fields = []
	writable_fields = []
	callable_methods = []

	for field in getattr(config, "readable_fields", []):
		field_codename = _build_permission_codename(
			app_label, model_name, "view", field_name=field
		)
		if _user_permission_override(user, field_codename) is False:
			continue

		if global_view or has_perm("view", field=field):
			readable_fields.append(field)

	for field in getattr(config, "writable_fields", []):
		change_codename = _build_permission_codename(
			app_label, model_name, "change", field_name=field
		)
		add_codename = _build_permission_codename(
			app_label, model_name, "add", field_name=field
		)
		if (
			_user_permission_override(user, change_codename) is False
			or _user_permission_override(user, add_codename) is False
		):
			continue

		if (
			global_change
			or global_add
			or has_perm("change", field=field)
			or has_perm("add", field=field)
		):
			writable_fields.append(field)

	# -----------------------------------------------------
	# Method permissions
	# -----------------------------------------------------

	for method in getattr(config, "callable_methods", []):
		method_codename = _build_permission_codename(
			app_label, model_name, "call", method_name=method
		)
		if _user_permission_override(user, method_codename) is False:
			continue
		if global_call or has_perm("call", method=method):
			callable_methods.append(method)

	return {
		"readable_fields": readable_fields,
		"writable_fields": writable_fields,
		"callable_methods": callable_methods,
		"global_permissions": {
			"view": global_view,
			"add": global_add,
			"change": global_change,
			"delete": global_delete,
			"call": global_call,
		},
	}


# ---------------------------------------------------------
# Permission Denial Response
# ---------------------------------------------------------

def _permission_denied(message):

	return JsonResponse(
		{
			"ok": False,
			"error": {
				"code": "permission_denied",
				"message": message,
			},
		},
		status=403,
	)


# ---------------------------------------------------------
# API Permission Decorator
# ---------------------------------------------------------

def restrict_api_by_role(force_action=None):
	"""
	Role-based access control for API endpoints.

	force_action:
		None   → auto detect from HTTP method
		read
		write
		delete
		call
	"""

	def decorator(view_func):

		@wraps(view_func)
		def wrapper(request, *args, **kwargs):

			# A Bearer token that no longer resolves is an authentication failure, not an
			# authorisation one. AuthenticationMiddleware sets request.user to None in that
			# case; answering 403 made an expired session indistinguishable from a genuine
			# permission denial, so no client could know to send the user back to login.
			if request.headers.get("Authorization", "").startswith("Bearer "):
				if getattr(request, "user", None) is None:
					return JsonResponse(
						{
							"ok": False,
							"error": {
								"code": "invalid_session",
								"message": "Session is invalid or has expired.",
							},
						},
						status=401,
					)

			model_name = kwargs.get("model_name")
	
			if not model_name:
				return JsonResponse(
					{
						"ok": False,
						"error": {
							"code": "missing_model_name",
							"message": "Route must include model_name.",
						},
					},
					status=400,
				)
		
			# -------------------------------------------------
			# Resolve model config
			# -------------------------------------------------

			try:
				config = model_registry.get(model_name)

			except ModelNotRegistered as exc:

				return JsonResponse(
					{
						"ok": False,
						"error": {
							"code": "model_not_registered",
							"message": str(exc),
						},
					},
					status=404,
				)
			
			# -------------------------------------------------
			# Resolve access
			# -------------------------------------------------

			access = _resolve_access(request.user, config)
			action = force_action or HTTP_ACTION_MAP.get(request.method)
			# -------------------------------------------------
			# Permission checks
			# -------------------------------------------------

			if action == "read" and not access["readable_fields"]:
				return _permission_denied(
					"You do not have permission to read this model."
				)

			if action == "write" and not access["writable_fields"]:
				return _permission_denied(
					"You do not have permission to write this model."
				)

			if action == "delete" and not access["global_permissions"]["delete"]:
				return _permission_denied(
					"You do not have permission to delete this model."
				)

			if action == "call" and not access["callable_methods"]:
				return _permission_denied(
					"You do not have permission to call actions on this model."
				)

			# -------------------------------------------------
			# Attach access to request
			# -------------------------------------------------

			request.api_model_config = config
			request.api_access = access

			return view_func(request, *args, **kwargs)

		return wrapper

	return decorator
