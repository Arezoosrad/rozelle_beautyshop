import functools
import logging
import threading
import time
from collections import defaultdict
from enum import Enum

from django.conf import settings
from django.utils.module_loading import import_string
from django.utils import timezone
from rozelle_beautyshop.core.models import HookLog

logger = logging.getLogger('core.hooks')

_lock = threading.Lock()

class PriorityLevel(Enum):
	tabindex=4
	HIGH = 5
	MEDIUM = 10
	LOW = 20

_hooks = defaultdict(list)  # {'hook_name': [{'func': func, 'priority': int, 'active': bool, 'once': bool, 'condition': callable}]}

def register_hook(hook_name, func, priority=PriorityLevel.MEDIUM.value, active=True, once=False, condition=None):
	with _lock:
		_hooks[hook_name].append({
			'func': func,
			'priority': priority,
			'active': active,
			'once': once,
			'condition': condition,
			'executed_for': set()
		})
		_hooks[hook_name].sort(key=lambda x: x['priority'])

def unregister_hook(hook_name, func):
	with _lock:
		_hooks[hook_name] = [h for h in _hooks[hook_name] if h['func'] != func]

def get_registered_hooks(hook_name):
	return [h['func'] for h in _hooks.get(hook_name, []) if h['active']]

def activate_hook(hook_name, func):
	with _lock:
		for h in _hooks.get(hook_name, []):
			if h['func'] == func:
				h['active'] = True

def deactivate_hook(hook_name, func):
	with _lock:
		for h in _hooks.get(hook_name, []):
			if h['func'] == func:
				h['active'] = False

def clear_hooks():
	with _lock:
		_hooks.clear()

def _check_condition(condition, *args, **kwargs):
	if not condition:
		return True
	try:
		return condition(*args, **kwargs)
	except Exception as e:
		logger.warning(f'Condition function failed: {e}')
		return False

def action_hook(hook_name=None, priority=PriorityLevel.MEDIUM.value, active=True, once=False, condition=None):
	def decorator(func):
		register_hook(hook_name or func.__name__, func, priority=priority, active=active, once=once, condition=condition)
		@functools.wraps(func)
		def wrapper(*args, **kwargs):
			return func(*args, **kwargs)
		return wrapper
	return decorator

def filter_hook(hook_name=None, priority=PriorityLevel.MEDIUM.value, active=True, once=False, condition=None):
	def decorator(func):
		register_hook(hook_name or func.__name__, func, priority=priority, active=active, once=once, condition=condition)
		@functools.wraps(func)
		def wrapper(value, *args, **kwargs):
			return func(value, *args, **kwargs)
		return wrapper
	return decorator

def run_hook(hook_name, *args, filter_value=None, **kwargs):
	results = []
	callbacks = _hooks.get(hook_name, [])
	callbacks = [cb for cb in callbacks if cb['active']]
	if not callbacks:
		return filter_value if filter_value is not None else []

	for cb in callbacks:
		func = cb['func']
		condition = cb.get('condition')
		once = cb.get('once')
		executed_for = cb.get('executed_for')

		if not _check_condition(condition, *args, **kwargs):
			continue

		if once:
			key = kwargs.get('user_id') or getattr(kwargs.get('user'), 'id', None) or 'global'
			if key in executed_for:
				continue
			executed_for.add(key)

		started_at = timezone.now()
		started_monotonic = time.monotonic()
		log_entry = HookLog(
			hook_name=hook_name,
			callback_name=func.__module__ + '.' + func.__name__,
			start_time=started_at,
		)
		try:
			if filter_value is not None:
				res = func(filter_value, *args, **kwargs)
				filter_value = res
			else:
				res = func(*args, **kwargs)
			log_entry.status = 'success'
			results.append(res)
		except Exception as e:
			log_entry.status = 'error'
			log_entry.error_message = str(e)
			logger.error(f'Hook {hook_name} callback {func} failed: {e}', exc_info=True)
			continue
		finally:
			log_entry.end_time = timezone.now()
			log_entry.execution_time_ms = (time.monotonic() - started_monotonic) * 1000
			log_entry.save()

		if hasattr(res, 'stop_hook_execution') and res.stop_hook_execution:
			break

	# ارسال event خارجی (مثال ساده لاگ)
	logger.info(f'Hook {hook_name} executed with {len(results)} callbacks.')

	return results if filter_value is None else filter_value

def list_hook_callbacks(hook_name):
	return [(cb['func'], cb['priority'], cb['active']) for cb in _hooks.get(hook_name, [])]

def async_run_hook(hook_name, *args, **kwargs):
	import threading
	t = threading.Thread(target=run_hook, args=(hook_name,) + args, kwargs=kwargs)
	t.start()
	return t
