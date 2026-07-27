from datetime import timedelta
import json
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone


def _model(app_label, model_name):
	return apps.get_model(app_label, model_name)


def _role_type(user):
	return getattr(getattr(user, 'role', None), 'role_type', None)


def is_admin(user):
	return bool(getattr(user, 'is_superuser', False) or _role_type(user) == 'admin')


def is_doctor(user):
	return _role_type(user) == 'expert' or hasattr(user, 'doctor_profile')


def is_patient(user):
	return _role_type(user) == 'customer' or hasattr(user, 'patient_profile')


def require_role(user, role):
	if role == 'admin' and not is_admin(user):
		raise PermissionError('دسترسی مدیر مورد نیاز است.')
	if role == 'doctor' and not (is_admin(user) or is_doctor(user)):
		raise PermissionError('دسترسی پزشک مورد نیاز است.')
	if role == 'patient' and not (is_admin(user) or is_patient(user)):
		raise PermissionError('دسترسی بیمار مورد نیاز است.')


def link_doctor_patient(doctor_user, patient_user, source='doctor'):
	require_role(doctor_user, 'doctor')
	doctor_profile = _model('clinical', 'doctor_profile').objects.get(user=doctor_user, is_active=True, trashed=False)
	patient_profile = _model('clinical', 'patient_profile').objects.get(user=patient_user, trashed=False)
	link, _ = _model('clinical', 'doctor_patient').objects.update_or_create(doctor=doctor_profile, patient=patient_profile, defaults={'source': source, 'status': 'active', 'trashed': False, 'last_activity_at': timezone.now()})
	return link


def resolve_patient_by_identifier(identifier):
	patient_profile = _model('clinical', 'patient_profile').objects.select_related('user').filter(user__national_code=identifier, trashed=False).first()
	if patient_profile is None:
		raise ValueError('بیماری با این کد ملی پیدا نشد.')
	return patient_profile.user


def _program_item_model():
	for model in apps.get_app_config('programs').get_models():
		field_names = {field.name for field in model._meta.local_fields}
		if {'program', 'exercise'}.issubset(field_names):
			return model
	raise LookupError('مدل آیتم برنامه پیدا نشد.')


def _copy_program_item(item_model, program, template_item):
	fields = {field.name for field in item_model._meta.local_fields}
	payload = {'program': program, 'exercise': template_item.exercise}
	mapping = {'level': template_item.level, 'sets': template_item.sets, 'repetitions': template_item.repetitions, 'duration_seconds': template_item.duration_seconds, 'rest_seconds': template_item.rest_seconds, 'sort_order': template_item.sort_order, 'doctor_notes': template_item.doctor_notes, 'days_mask': template_item.days_mask}
	for key, value in mapping.items():
		if key in fields:
			payload[key] = value
	return item_model.objects.create(**payload)


def prescribe_program(doctor_user, patient_user, template_id=None, title='', level=None, days_per_week=None, duration_weeks=None, doctor_instructions=''):
	require_role(doctor_user, 'doctor')
	link_doctor_patient(doctor_user, patient_user, source='doctor')
	Program = _model('programs', 'patient_program')
	Template = _model('programs', 'program_template')
	template = None
	if template_id:
		template = Template.objects.prefetch_related('items__exercise').get(pk=template_id, trashed=False, is_active=True)
		if template.doctor_id not in (None, doctor_user.pk) and not template.is_shared:
			raise PermissionError('این قالب متعلق به پزشک دیگری است.')
		program_defaults = {'title': title or template.title, 'doctor_instructions': doctor_instructions or template.description, 'level': level or template.level, 'days_per_week': days_per_week or template.default_days_per_week, 'duration_weeks': duration_weeks or template.default_duration_weeks}
	else:
		program_defaults = {'title': title or 'برنامه تمرینی', 'doctor_instructions': doctor_instructions, 'level': level or 'beginner', 'days_per_week': days_per_week or 3, 'duration_weeks': duration_weeks or 4}
	program = Program.objects.create(patient=patient_user, doctor=doctor_user, template=template, status='draft', registration_channel='clinic', **program_defaults)
	if template:
		item_model = _program_item_model()
		for item in template.items.filter(trashed=False).order_by('sort_order'):
			_copy_program_item(item_model, program, item)
	return program


def mark_program_ready(doctor_user, program_id):
	require_role(doctor_user, 'doctor')
	Program = _model('programs', 'patient_program')
	program = Program.objects.get(pk=program_id, doctor=doctor_user, trashed=False)
	if not program.items.filter(trashed=False).exists():
		raise ValueError('برنامه بدون تمرین قابل فعال‌سازی نیست.')
	program.status = 'active'
	program.ready_notified_at = timezone.now()
	program.start_date = program.start_date or timezone.localdate()
	if program.end_date is None:
		program.end_date = program.start_date + timedelta(weeks=program.duration_weeks)
	program.save(update_fields=['status', 'ready_notified_at', 'start_date', 'end_date', 'updated_at'])
	try:
		from tracking.services import notify_program_ready
		notify_program_ready(program.patient, program.pk)
	except Exception:
		# Activation itself must remain successful even if notification delivery fails.
		pass
	return program


def calculate_discount(discount, amount):
	amount = Decimal(amount)
	if not discount.is_valid():
		raise ValueError('کد تخفیف معتبر نیست.')
	if discount.discount_type == 'percent':
		discount_amount = amount * discount.value / Decimal('100')
		if discount.max_discount is not None:
			discount_amount = min(discount_amount, discount.max_discount)
	else:
		discount_amount = discount.value
	return max(Decimal('0'), min(amount, discount_amount))


def create_subscription(patient_user, plan_id, discount_code_text=''):
	Subscription = _model('billing', 'subscription')
	Plan = _model('packages', 'package_plan')
	Discount = _model('billing', 'discount_code')
	require_role(patient_user, 'patient')
	with transaction.atomic():
		plan = Plan.objects.select_related('package').get(pk=plan_id, is_active=True, trashed=False, package__is_published=True, package__trashed=False)
		discount = None
		discount_amount = Decimal('0')
		if discount_code_text:
			try:
				discount = Discount.objects.select_for_update().get(code=discount_code_text.strip().upper(), trashed=False)
			except Discount.DoesNotExist as exc:
				raise ValueError('کد تخفیف معتبر نیست.') from exc
			discount_amount = calculate_discount(discount, plan.price)
		subscription = Subscription.objects.create(patient=patient_user, package=plan.package, plan=plan, discount_code=discount, original_amount=plan.price, discount_amount=discount_amount, payable_amount=plan.price - discount_amount, status='pending')
		if discount is not None:
			discount.used_count += 1
			discount.save(update_fields=['used_count', 'updated_at'])
	return subscription


def send_message(sender, conversation_id, body):
	Conversation = _model('messaging', 'conversation')
	Message = _model('messaging', 'message')
	conversation = Conversation.objects.get(pk=conversation_id, trashed=False)
	if sender.pk not in (conversation.doctor_id, conversation.patient_id):
		raise PermissionError('این گفت‌وگو متعلق به کاربر نیست.')
	if not body or not body.strip():
		raise ValueError('متن پیام خالی است.')
	with transaction.atomic():
		message = Message.objects.create(sender=sender, conversation=conversation, body=body.strip())
		conversation.last_message_at = timezone.now()
		conversation.save(update_fields=['last_message_at', 'updated_at'])
	return message


def complete_assessment(patient_user, assessment_id, answers):
	Assessment = _model('assessments', 'patient_assessment')
	Answer = _model('assessments', 'assessment_answer')
	Option = _model('assessments', 'question_option')
	with transaction.atomic():
		assessment = Assessment.objects.select_related('questionnaire').prefetch_related('questionnaire__questions__options').get(pk=assessment_id, patient=patient_user, trashed=False)
		if assessment.status != 'draft':
			raise ValueError('این ارزیابی قابل ویرایش نیست.')
		questions = {question.pk: question for question in assessment.questionnaire.questions.filter(trashed=False)}
		for question in questions.values():
			if question.is_required and str(question.pk) not in answers and question.pk not in answers:
				raise ValueError(f'پاسخ سؤال «{question.text[:40]}» الزامی است.')
		Answer.objects.filter(assessment=assessment).delete()
		total = Decimal('0')
		for raw_question_id, value in answers.items():
			question = questions.get(int(raw_question_id))
			if question is None:
				raise ValueError('سؤال متعلق به این پرسشنامه نیست.')
			if question.question_type in ('single', 'multiple'):
				option_ids = value if isinstance(value, list) else [value]
				if question.question_type == 'single' and len(option_ids) != 1:
					raise ValueError('سؤال تک‌گزینه‌ای فقط یک گزینه می‌پذیرد.')
				options = list(Option.objects.filter(question=question, pk__in=option_ids, is_active=True, trashed=False))
				if len(options) != len(set(option_ids)):
					raise ValueError('گزینه پاسخ معتبر نیست.')
				score = sum((option.score for option in options), Decimal('0'))
				Answer.objects.create(assessment=assessment, question=question, option=options[0] if options else None, answer_text=json.dumps([option.pk for option in options], ensure_ascii=False) if question.question_type == 'multiple' else '', score=score)
				total += score
				continue
			payload = {'assessment': assessment, 'question': question, 'score': Decimal('0')}
			if question.question_type == 'number':
				payload['answer_number'] = Decimal(str(value))
			elif question.question_type == 'boolean':
				if not isinstance(value, bool):
					raise ValueError('پاسخ بله/خیر باید boolean باشد.')
				payload['answer_boolean'] = value
			else:
				payload['answer_text'] = str(value)
			Answer.objects.create(**payload)
		assessment.total_score = total
		assessment.status = 'completed'
		assessment.completed_at = timezone.now()
		assessment.save(update_fields=['total_score', 'status', 'completed_at', 'updated_at'])
	return assessment


def book_appointment(patient_user, doctor_user, starts_at, patient_note=''):
	require_role(patient_user, 'patient')
	doctor_profile = _model('clinical', 'doctor_profile').objects.get(user=doctor_user, is_active=True, is_verified=True, trashed=False)
	if not _model('clinical', 'doctor_patient').objects.filter(doctor=doctor_profile, patient__user=patient_user, status='active', trashed=False).exists():
		raise PermissionError('این پزشک به بیمار اختصاص داده نشده است.')
	Availability = _model('appointments', 'doctor_availability')
	Appointment = _model('appointments', 'appointment')
	local_date = timezone.localtime(starts_at).date()
	weekday = (local_date.weekday() + 2) % 7
	clock = timezone.localtime(starts_at).time()
	availability = Availability.objects.filter(doctor=doctor_user, weekday=weekday, is_active=True, trashed=False, start_time__lte=clock, end_time__gt=clock).order_by('start_time').first()
	if availability is None:
		raise ValueError('این زمان در برنامه کاری پزشک نیست.')
	ends_at = starts_at + timedelta(minutes=availability.slot_minutes)
	if timezone.localtime(ends_at).time() > availability.end_time:
		raise ValueError('زمان انتخاب‌شده خارج از بازه کاری پزشک است.')
	if Appointment.objects.filter(doctor=doctor_user, starts_at=starts_at, status__in=['pending', 'confirmed']).exists():
		raise ValueError('این زمان قبلاً رزرو شده است.')
	return Appointment.objects.create(patient=patient_user, doctor=doctor_user, starts_at=starts_at, ends_at=ends_at, patient_note=patient_note, status='pending')


def admin_dashboard(admin_user):
	require_role(admin_user, 'admin')
	User = _model('profiles', 'user_profile')
	Doctor = _model('clinical', 'doctor_profile')
	Subscription = _model('billing', 'subscription')
	Payment = _model('billing', 'payment')
	Package = _model('packages', 'package')
	return {'user_count': User.objects.filter(trashed=False).count(), 'doctor_count': Doctor.objects.filter(is_active=True, trashed=False).count(), 'active_subscription_count': Subscription.objects.filter(status='active', trashed=False).count(), 'published_package_count': Package.objects.filter(is_published=True, trashed=False).count(), 'revenue': Payment.objects.filter(status='success', trashed=False).aggregate(total=Sum('amount'))['total'] or Decimal('0')}


def doctor_dashboard(doctor_user):
	require_role(doctor_user, 'doctor')
	Link = _model('clinical', 'doctor_patient')
	Settlement = _model('billing', 'doctor_settlement')
	Program = _model('programs', 'patient_program')
	month_start = timezone.localdate().replace(day=1)
	return {'patient_count_total': Link.objects.filter(doctor__user=doctor_user, status='active', trashed=False).count(), 'patient_count_month': Link.objects.filter(doctor__user=doctor_user, created_at__date__gte=month_start, trashed=False).count(), 'active_programs': Program.objects.filter(doctor=doctor_user, status='active', trashed=False).count(), 'payable_amount': Settlement.objects.filter(doctor=doctor_user, status='pending', period_start__gte=month_start, trashed=False).aggregate(total=Sum('payable_amount'))['total'] or Decimal('0')}


def patient_dashboard(patient_user):
	require_role(patient_user, 'patient')
	Program = _model('programs', 'patient_program')
	Reminder = _model('reminders', 'reminder')
	Subscription = _model('billing', 'subscription')
	Assessment = _model('assessments', 'patient_assessment')
	Conversation = _model('messaging', 'conversation')
	return {'active_programs': Program.objects.filter(patient=patient_user, status='active', trashed=False).count(), 'active_reminders': Reminder.objects.filter(patient=patient_user, is_active=True, trashed=False).count(), 'active_subscriptions': Subscription.objects.filter(patient=patient_user, status='active', trashed=False).count(), 'pending_assessments': Assessment.objects.filter(patient=patient_user, status='draft', trashed=False).count(), 'open_conversations': Conversation.objects.filter(patient=patient_user, status='open', trashed=False).count()}
