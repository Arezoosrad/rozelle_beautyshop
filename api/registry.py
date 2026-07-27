# your_app/api/registry.py

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set, Type
from django.core.exceptions import FieldDoesNotExist
from django.db import models

class ApiModel:
	"""
	هر مدلی که بخواهد به صورت خودکار در API ثبت شود، این کلاس را ارث‌بری می‌کند.

	تنظیمات با override کردن classmethodهای زیر انجام می‌شود.
	فقط مواردی را که نیاز داری بازنویسی کن؛ بقیه مقدار پیش‌فرض دارند.
	"""

	# پرچم شناسایی برای autodiscover
	api_exposed = True

	@classmethod
	def api_name(cls):
		"""نام مدل در API (پیش‌فرض: نام کلاس با حروف کوچک)"""
		return cls.__name__.lower()

	@classmethod
	def api_readable_fields(cls):
		return []

	@classmethod
	def api_writable_fields(cls):
		return []

	@classmethod
	def api_callable_methods(cls):
		return []

	@classmethod
	def api_lookup_field(cls):
		return "id"

	@classmethod
	def api_soft_delete_field(cls):
		return "trashed"

	@classmethod
	def api_ordering_fields(cls):
		return None

	@classmethod
	def api_search_fields(cls):
		return None

	@classmethod
	def api_collection_queryset(cls, request, queryset, access):
		return queryset

	@classmethod
	def api_single_queryset(cls, request, queryset, access):
		return queryset

	@classmethod
	def api_can_write(cls, request, obj=None, payload=None):
		"""Veto a create or update.

		Return None to allow, or (code, message) to refuse with 403. Model-level permissions
		answer "may this role write this model"; this answers "may this particular record be
		written right now" -- a locked report, a protected permission row.
		"""
		return None


class RegistryError(Exception):
	"""
	خطای داخلی مربوط به رجیستری API.
	این خطا معمولاً نباید مستقیماً به کاربر نمایش داده شود.
	"""
	pass


class ModelNotRegistered(RegistryError):
	"""
	وقتی model_name در رجیستری ثبت نشده باشد.
	"""
	pass


class InvalidRegistryConfig(RegistryError):
	"""
	وقتی تنظیمات ثبت مدل معتبر نباشد.
	"""
	pass


@dataclass(frozen=True)
class ModelApiConfig:
	"""
	تنظیمات API برای یک مدل خاص.

	این کلاس فقط قرارداد دسترسی مدل را نگه می‌دارد.
	منطق permission، serialization و validation در فایل‌های دیگر انجام می‌شود.
	"""

	model: Type[models.Model]

	readable_fields: Set[str] = field(default_factory=set)
	writable_fields: Set[str] = field(default_factory=set)
	callable_methods: Set[str] = field(default_factory=set)

	lookup_field: str = "id"
	soft_delete_field: str = "trashed"

	# اگر در آینده خواستی برای ordering محدودیت جدا داشته باشی.
	# اگر None باشد، از readable_fields استفاده می‌کنیم.
	ordering_fields: Optional[Set[str]] = None
	search_fields: Optional[Set[str]] = None
	# اگر خواستی نام نمایشی مدل با key رجیستری متفاوت باشد.
	name: Optional[str] = None

	def get_ordering_fields(self) -> Set[str]:
		"""
		فیلدهای مجاز برای ordering.
		اگر صراحتاً تعریف نشده باشد، فیلدهای خواندنی مبنا هستند.
		"""
		if self.ordering_fields is None:
			return set(self.readable_fields)
		return set(self.ordering_fields)

	def can_read_field(self, field_name: str) -> bool:
		return field_name in self.readable_fields

	def can_write_field(self, field_name: str) -> bool:
		return field_name in self.writable_fields

	def can_call_method(self, method_name: str) -> bool:
		return method_name in self.callable_methods

	def has_soft_delete(self) -> bool:
		"""
		بررسی می‌کند آیا مدل واقعاً فیلد soft delete را دارد یا نه.
		"""
		if not self.soft_delete_field:
			return False

		try:
			self.model._meta.get_field(self.soft_delete_field)
			return True
		except FieldDoesNotExist:
			return False


class ApiRegistry:
	"""
	رجیستری مرکزی API.

	فقط مدل‌هایی که اینجا ثبت شوند از طریق API قابل دسترسی هستند.
	"""

	def __init__(self):
		self._items: Dict[str, ModelApiConfig] = {}

	def register(
		self,
		name: str,
		model: Type[models.Model],
		readable_fields: Iterable[str],
		writable_fields: Iterable[str] = (),
		callable_methods: Iterable[str] = (),
		lookup_field: str = "id",
		soft_delete_field: str = "trashed",
		ordering_fields: Optional[Iterable[str]] = None,
		search_fields: Optional[Iterable[str]] = None,
	) -> ModelApiConfig:
		"""
		ثبت یک مدل در API.

		مثال:

			api_registry.register(
				name="customer",
				model=Customer,
				readable_fields=["id", "first_name", "last_name", "full_name"],
				writable_fields=["first_name", "last_name"],
				callable_methods=["send_otp"],
				lookup_field="slug",
			)
		"""

		name = self._normalize_name(name)

		readable_fields_set = self._to_clean_set(readable_fields)
		writable_fields_set = self._to_clean_set(writable_fields)
		callable_methods_set = self._to_clean_set(callable_methods)

		if ordering_fields is None:
			ordering_fields_set = None
		else:
			ordering_fields_set = self._to_clean_set(ordering_fields)

		if search_fields is None:
			search_fields_set = None
		else:
			search_fields_set = self._to_clean_set(search_fields)

		config = ModelApiConfig(
			name=name,
			model=model,
			readable_fields=readable_fields_set,
			writable_fields=writable_fields_set,
			callable_methods=callable_methods_set,
			lookup_field=lookup_field,
			soft_delete_field=soft_delete_field,
			ordering_fields=ordering_fields_set,
			search_fields=search_fields_set,
		)

		self._validate_config(name, config)

		self._items[name] = config

		return config

	def get(self, name: str) -> ModelApiConfig:
		"""
		گرفتن تنظیمات مدل از رجیستری.

		اگر مدل ثبت نشده باشد، ModelNotRegistered پرتاب می‌شود.
		"""

		name = self._normalize_name(name)

		try:
			return self._items[name]
		except KeyError:
			raise ModelNotRegistered(f"Model '{name}' is not registered in API registry.")

	def exists(self, name: str) -> bool:
		"""
		بررسی اینکه آیا مدل در رجیستری ثبت شده یا نه.
		"""

		name = self._normalize_name(name)
		return name in self._items

	def all(self) -> Dict[str, ModelApiConfig]:
		"""
		خروجی read-only سبک از کل رجیستری.

		خود dict داخلی برگردانده نمی‌شود تا از تغییر ناخواسته جلوگیری شود.
		"""
		return dict(self._items)

	def names(self) -> Set[str]:
		"""
		نام مدل‌های ثبت‌شده.
		"""
		return set(self._items.keys())

	def _validate_config(self, name: str, config: ModelApiConfig) -> None:
		"""
		اعتبارسنجی تنظیمات رجیستری.

		اینجا عمداً سخت‌گیر هستیم تا خطاها زمان start پروژه مشخص شوند،
		نه وسط request واقعی کاربر.
		"""

		if not name:
			raise InvalidRegistryConfig("Registry name cannot be empty.")

		if name in self._items:
			raise InvalidRegistryConfig(f"Model name '{name}' is already registered.")

		if not issubclass(config.model, models.Model):
			raise InvalidRegistryConfig(f"'{name}' model must be a Django model.")

		if not config.readable_fields:
			raise InvalidRegistryConfig(f"'{name}' must have at least one readable field.")

		self._validate_lookup_field(name, config)
		self._validate_readable_fields(name, config)
		self._validate_writable_fields(name, config)
		self._validate_callable_methods(name, config)
		self._validate_ordering_fields(name, config)
		self._validate_soft_delete_field(name, config)
		self._validate_search_fields(name, config)


	def _validate_lookup_field(self, name: str, config: ModelApiConfig) -> None:
		"""
		lookup_field باید یک فیلد واقعی مدل باشد.

		چون قرار است با آن کوئری دیتابیس زده شود،
		بهتر است متد/property نباشد.
		"""

		try:
			config.model._meta.get_field(config.lookup_field)
		except FieldDoesNotExist:
			raise InvalidRegistryConfig(
				f"'{name}' lookup_field '{config.lookup_field}' does not exist on model."
			)

	def _validate_readable_fields(self, name: str, config: ModelApiConfig) -> None:
		"""
		readable_fields می‌تواند شامل:
		- فیلد واقعی مدل
		- property
		- method بدون آرگومان مثل full_name
		باشد.

		بنابراین فقط بررسی می‌کنیم attribute روی مدل وجود داشته باشد
		یا فیلد واقعی مدل باشد.
		"""

		for field_name in config.readable_fields:
			if self._is_dangerous_name(field_name):
				raise InvalidRegistryConfig(
					f"'{name}' readable field '{field_name}' is not allowed."
				)

			if not self._model_has_field_or_attr(config.model, field_name):
				raise InvalidRegistryConfig(
					f"'{name}' readable field '{field_name}' does not exist."
				)

	def _validate_writable_fields(self, name: str, config: ModelApiConfig) -> None:
		"""
		writable_fields فقط باید فیلد واقعی دیتابیس باشد.

		متد، property و فیلدهای many-to-many پیچیده را فعلاً ساده نگه می‌داریم.
		اگر بعداً خواستی M2M هم پشتیبانی شود، جداگانه اضافه می‌کنیم.
		"""

		for field_name in config.writable_fields:
			if self._is_dangerous_name(field_name):
				raise InvalidRegistryConfig(
					f"'{name}' writable field '{field_name}' is not allowed."
				)

			try:
				model_field = config.model._meta.get_field(field_name)
			except FieldDoesNotExist:
				raise InvalidRegistryConfig(
					f"'{name}' writable field '{field_name}' does not exist as a model field."
				)

			if not getattr(model_field, "editable", False):
				raise InvalidRegistryConfig(
					f"'{name}' writable field '{field_name}' is not editable."
				)

			if model_field.many_to_many:
				raise InvalidRegistryConfig(
					f"'{name}' writable field '{field_name}' is ManyToMany. "
					"ManyToMany writes are not supported in this simple registry."
				)

	def _validate_callable_methods(self, name: str, config: ModelApiConfig) -> None:
		"""
		callable_methods فقط باید متد واقعی مدل باشند.

		متدهای خطرناک مثل delete/save/full_clean و نام‌های خصوصی ممنوع هستند.
		"""

		for method_name in config.callable_methods:
			if self._is_dangerous_name(method_name):
				raise InvalidRegistryConfig(
					f"'{name}' callable method '{method_name}' is not allowed."
				)

			attr = getattr(config.model, method_name, None)

			if attr is None:
				raise InvalidRegistryConfig(
					f"'{name}' callable method '{method_name}' does not exist."
				)

			if not callable(attr):
				raise InvalidRegistryConfig(
					f"'{name}' callable method '{method_name}' is not callable."
				)

	def _validate_ordering_fields(self, name: str, config: ModelApiConfig) -> None:
		"""
		ordering فقط روی فیلدهایی مجاز است که در readable_fields هم باشند.

		برای سادگی، ordering روی method/property را هم ممنوع می‌کنیم؛
		چون دیتابیس نمی‌تواند مستقیماً با method مرتب کند.
		"""

		ordering_fields = config.get_ordering_fields()

		for field_name in ordering_fields:
			clean_name = field_name.lstrip("-")

			if clean_name not in config.readable_fields:
				raise InvalidRegistryConfig(
					f"'{name}' ordering field '{clean_name}' must be readable."
				)

			try:
				config.model._meta.get_field(clean_name)
			except FieldDoesNotExist:
				raise InvalidRegistryConfig(
					f"'{name}' ordering field '{clean_name}' must be a real model field."
				)

	def _validate_soft_delete_field(self, name: str, config: ModelApiConfig) -> None:
		"""
		soft_delete_field اگر وجود داشته باشد بهتر است BooleanField باشد.

		اگر مدل soft delete ندارد، می‌توانی soft_delete_field را None یا "" بدهی.
		"""

		if not config.soft_delete_field:
			return

		try:
			model_field = config.model._meta.get_field(config.soft_delete_field)
		except FieldDoesNotExist:
			# این را خطا نمی‌گیریم تا مدل‌هایی که soft delete ندارند هم قابل ثبت باشند.
			# هنگام delete در views.py بررسی می‌کنیم.
			return

		if not isinstance(model_field, models.BooleanField):
			raise InvalidRegistryConfig(
				f"'{name}' soft_delete_field '{config.soft_delete_field}' must be BooleanField."
			)

	def _validate_search_fields(self, name: str, config: ModelApiConfig) -> None:
		"""
		search_fields باید subset از readable_fields باشد
		و باید attribute واقعی مدل باشد.
		"""

		if not config.search_fields:
			return

		for field_name in config.search_fields:

			if field_name not in config.readable_fields:
				raise InvalidRegistryConfig(
					f"'{name}' search field '{field_name}' must be in readable_fields."
				)

			if not self._model_has_field_or_attr(config.model, field_name):
				raise InvalidRegistryConfig(
					f"'{name}' search field '{field_name}' does not exist on model."
				)

	def _normalize_name(self, name: str) -> str:
		"""
		نرمال‌سازی نام مدل در URL.

		customer، Customer و CUSTOMER همگی customer می‌شوند.
		"""

		if name is None:
			return ""

		return str(name).strip().lower()

	def _to_clean_set(self, values: Iterable[str]) -> Set[str]:
		"""
		تبدیل لیست/تاپل به set تمیز.
		"""

		result = set()

		for value in values:
			if value is None:
				continue

			value = str(value).strip()

			if value:
				result.add(value)

		return result

	def _model_has_field_or_attr(self, model: Type[models.Model], name: str) -> bool:
		"""
		بررسی وجود فیلد واقعی یا attribute/property/method روی مدل.
		"""

		try:
			model._meta.get_field(name)
			return True
		except FieldDoesNotExist:  # ← تغییر از ObjectDoesNotExist به FieldDoesNotExist
			pass

		return hasattr(model, name)

	def _is_dangerous_name(self, name: str) -> bool:
		"""
		جلوگیری از ثبت نام‌های خطرناک.

		این بخش محافظت اضافه است تا کسی اشتباهی متدهایی مثل delete/save
		یا attributeهای خصوصی را whitelist نکند.
		"""

		if not name:
			return True

		if name.startswith("_"):
			return True

		dangerous_names = {
			# عملیات حساس مدل
			"save",
			"delete",
			"adelete",
			"asave",
			"full_clean",
			"clean",
			"validate_unique",
			"refresh_from_db",

			# introspection / internals
			"__class__",
			"__dict__",
			"__getattribute__",
			"__setattr__",
			"__delattr__",
			"__init__",
			"__new__",

			# Django internals
			"_meta",
			"objects",
			"pk",
		}

		return name in dangerous_names

	def autodiscover(self):
		"""
		همه مدل‌های ثبت‌شده در Django را پیمایش می‌کند و هر مدلی که
		از ApiModel ارث‌بری کرده باشد (api_exposed=True) را خودکار ثبت می‌کند.

		باید بعد از بارگذاری کامل اپ‌ها صدا زده شود (در AppConfig.ready).
		"""
		from django.apps import apps

		registered = []
		for model in apps.get_models():
			# فقط مدل‌هایی که صراحتاً علامت‌گذاری شده‌اند
			if not getattr(model, "api_exposed", False):
				continue

			name = model.api_name()

			# جلوگیری از ثبت دوباره اگر autodiscover چند بار صدا زده شود
			if self.exists(name):
				continue

			self.register(
				name=name,
				model=model,
				readable_fields=model.api_readable_fields(),
				writable_fields=model.api_writable_fields(),
				callable_methods=model.api_callable_methods(),
				lookup_field=model.api_lookup_field(),
				soft_delete_field=model.api_soft_delete_field(),
				ordering_fields=model.api_ordering_fields(),
				search_fields=model.api_search_fields(),
			)
			registered.append(name)

		return registered

model_registry = ApiRegistry()