# core/utils.py
from datetime import datetime, time, timedelta
import re

from django.utils import timezone

class StopHookExecution:
	tabindex=4
	stop_hook_execution = True
	def __init__(self, result=None):
		self.result = result
		
def parse_cron_expression(expr):
	# پشتیبانی از چند نمونه ساده (می‌توانید کتابhome crontab را استفاده کنید برای حرفه‌ای تر)
	expr = expr.strip().lower()
	if expr == 'daily':
		return timedelta(days=1)
	elif expr == 'hourly':
		return timedelta(hours=1)
	elif expr == 'weekly':
		return timedelta(weeks=1)
	elif match := re.match(r'^\*/(\d+) \* \* \* \*$', expr):
		minutes = int(match.group(1))
		if minutes <= 0:
			raise ValueError('cron minute interval must be positive')
		return timedelta(minutes=minutes)
	elif re.match(r'^\d+ \d+ \* \* \*$', expr):
		# ساده‌ترین حالت مثلا 0 0 * * * ساعت 00:00 هر روز
		parts = expr.split()
		hour, minute = int(parts[1]), int(parts[0])
		return hour, minute
	else:
		return timedelta(days=1)  # پیش‌فرض روزانه

def should_run(cron_job):
	now = timezone.localtime()
	
	if not cron_job.next_run:
		return True

	next_run_dt = timezone.make_aware(
		datetime.combine(cron_job.next_run, cron_job.next_run_time or time.min),
		timezone.get_current_timezone(),
	)

	return now >= next_run_dt

def update_next_run(cron_job):
	now = timezone.localtime()
	interval = parse_cron_expression(cron_job.cron_expression)
	if isinstance(interval, timedelta):
		next_run = now + interval
	elif isinstance(interval, tuple):
		hour, minute = interval
		next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
		if next_run <= now:
			next_run += timedelta(days=1)
	else:
		next_run = now + timedelta(days=1)
	cron_job.next_run = next_run.date()
	cron_job.next_run_time = next_run.time().replace(tzinfo=None)
	cron_job.save(update_fields=['next_run', 'next_run_time'])
