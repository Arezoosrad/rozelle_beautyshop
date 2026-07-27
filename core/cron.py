# core/cron.py
import functools


CRON_REGISTRY = {}

def cron_job(name=None, cron_expression='daily', active=True):
	def decorator(func):
		job_name = name or func.__name__
		func_path = f'{func.__module__}.{func.__name__}'
		CRON_REGISTRY[job_name] = {
			'func_path': func_path,
			'cron_expression': cron_expression,
			'active': active,
		}
		@functools.wraps(func)
		def wrapper(*args, **kwargs):
			return func(*args, **kwargs)
		return wrapper
	return decorator
