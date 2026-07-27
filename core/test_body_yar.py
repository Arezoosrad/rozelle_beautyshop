from datetime import datetime, time
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from api.body_yar_access import scope_queryset
from appointments.models import appointment, doctor_availability
from assessments.models import patient_assessment, question_option, questionnaire, questionnaire_question
from billing.models import discount_code
from clinical.models import doctor_patient, doctor_profile, patient_profile
from content.models import article, course
from exercises.models import exercise, exercise_category
from messaging.models import conversation, message
from packages.models import package, package_plan
from profiles.models import user_profile, user_role
from programs.models import patient_program, program_template, program_template_item
from .body_yar import (
	admin_dashboard,
	book_appointment,
	complete_assessment,
	create_subscription,
	doctor_dashboard,
	link_doctor_patient,
	mark_program_ready,
	patient_dashboard,
	prescribe_program,
	resolve_patient_by_identifier,
	send_message,
)


class BodyYarFlowTestCase(TestCase):
	def setUp(self):
		self.client = Client()
		self.admin_role = user_role.objects.create(name='admin', name_fa='مدیر', role_type='admin', level=1, is_system_role=True)
		self.doctor_role = user_role.objects.create(name='doctor', name_fa='پزشک', role_type='expert', level=20)
		self.patient_role = user_role.objects.create(name='patient', name_fa='بیمار', role_type='customer', level=50)
		self.admin = user_profile.objects.create_user(email='admin@example.com', password='Secret123!', role=self.admin_role, is_staff=True, is_superuser=True)
		self.doctor = user_profile.objects.create_user(email='doctor@example.com', password='Secret123!', first_name='دکتر', last_name='آزمون', role=self.doctor_role, phone='09120000001', is_verified=True)
		self.patient = user_profile.objects.create_user(email='patient@example.com', password='Secret123!', first_name='بیمار', last_name='آزمون', role=self.patient_role, phone='09120000002', national_code='0012345678', is_verified=True)
		doctor_profile.objects.create(user=self.doctor, code='DR-TEST', specialty='فیزیوتراپی', is_verified=True)
		patient_profile.objects.create(user=self.patient, occupation='برنامه‌نویس')
		self.category = exercise_category.objects.create(name='عمومی', slug='general')
		self.exercise = exercise.objects.create(name_fa='کشش گردن', name_en='Neck Stretch', slug='neck-stretch', category=self.category, instructions='سر را آرام به طرفین حرکت دهید.', warnings='در صورت درد شدید انجام نشود.', is_published=True)

	def login(self, user):
		self.client.force_login(user)

	def test_patient_identifier_resolves_to_profile_user(self):
		self.assertEqual(resolve_patient_by_identifier('0012345678'), self.patient)

	def test_doctor_can_link_patient(self):
		link = link_doctor_patient(self.doctor, self.patient)
		self.assertEqual(link.doctor.user_id, self.doctor.pk)
		self.assertEqual(link.patient.user_id, self.patient.pk)
		self.assertEqual(doctor_patient.objects.count(), 1)

	def test_doctor_can_prescribe_template_and_copy_items(self):
		template = program_template.objects.create(title='برنامه گردن', doctor=self.doctor, level='beginner', default_days_per_week=4, default_duration_weeks=6)
		program_template_item.objects.create(template=template, exercise=self.exercise, sets=2, repetitions=12, duration_seconds=0, rest_seconds=30, sort_order=1)
		program = prescribe_program(self.doctor, self.patient, template_id=template.pk)
		self.assertEqual(program.patient_id, self.patient.pk)
		self.assertEqual(program.doctor_id, self.doctor.pk)
		self.assertEqual(program.status, 'draft')
		self.assertEqual(program.items.count(), 1)
		self.assertEqual(program.items.first().exercise_id, self.exercise.pk)

	def test_program_cannot_be_ready_without_items(self):
		program = patient_program.objects.create(patient=self.patient, doctor=self.doctor, title='خالی', status='draft')
		with self.assertRaisesMessage(ValueError, 'بدون تمرین'):
			mark_program_ready(self.doctor, program.pk)

	def test_program_ready_flow_activates_program(self):
		program = patient_program.objects.create(patient=self.patient, doctor=self.doctor, title='برنامه', status='draft')
		program.items.create(exercise=self.exercise)
		mark_program_ready(self.doctor, program.pk)
		program.refresh_from_db()
		self.assertEqual(program.status, 'active')
		self.assertIsNotNone(program.ready_notified_at)

	def test_discounted_subscription_uses_plan_price_and_discount(self):
		pkg = package.objects.create(title='پکیج پیشگیری', is_published=True)
		plan = package_plan.objects.create(package=pkg, title='یک ماهه', duration_months=1, price=100000)
		discount_code.objects.create(code='TEST10', title='ده درصد', discount_type='percent', value=10)
		sub = create_subscription(self.patient, plan.pk, 'test10')
		self.assertEqual(sub.original_amount, Decimal('100000'))
		self.assertEqual(sub.discount_amount, Decimal('10000'))
		self.assertEqual(sub.payable_amount, Decimal('90000'))
		self.assertEqual(sub.status, 'pending')

	def test_invalid_discount_is_rejected(self):
		pkg = package.objects.create(title='پکیج', is_published=True)
		plan = package_plan.objects.create(package=pkg, title='پلن', duration_months=1, price=100000)
		with self.assertRaisesMessage(Exception, 'کد تخفیف معتبر نیست'):
			create_subscription(self.patient, plan.pk, 'UNKNOWN')

	def test_assessment_completion_validates_required_questions_and_scores(self):
		q = questionnaire.objects.create(title='ارزیابی', assessment_type='general')
		question = questionnaire_question.objects.create(questionnaire=q, text='درد دارید؟', question_type='single', is_required=True, score_enabled=True)
		option = question_option.objects.create(question=question, label='خیر', value='no', score=0)
		assessment = patient_assessment.objects.create(patient=self.patient, questionnaire=q)
		complete_assessment(self.patient, assessment.pk, {str(question.pk): option.pk})
		assessment.refresh_from_db()
		self.assertEqual(assessment.status, 'completed')
		self.assertEqual(assessment.total_score, Decimal('0'))
		self.assertEqual(assessment.answers.count(), 1)

	def test_assessment_rejects_missing_required_answer(self):
		q = questionnaire.objects.create(title='ارزیابی', assessment_type='general')
		questionnaire_question.objects.create(questionnaire=q, text='سؤال اجباری', question_type='text', is_required=True)
		assessment = patient_assessment.objects.create(patient=self.patient, questionnaire=q)
		with self.assertRaisesMessage(ValueError, 'الزامی'):
			complete_assessment(self.patient, assessment.pk, {})

	def test_patient_can_send_message_only_in_own_conversation(self):
		conversation_obj = conversation.objects.create(doctor=self.doctor, patient=self.patient, subject='پیگیری')
		created = send_message(self.patient, conversation_obj.pk, 'سلام')
		self.assertEqual(created.sender_id, self.patient.pk)
		self.assertEqual(message.objects.count(), 1)
		conversation_obj.refresh_from_db()
		self.assertIsNotNone(conversation_obj.last_message_at)

	def test_patient_dashboard_counts_core_objects(self):
		patient_program.objects.create(patient=self.patient, doctor=self.doctor, title='برنامه', status='active')
		self.login(self.patient)
		response = self.client.get(reverse('api:body_yar_patient_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['data']['active_programs'], 1)

	def test_doctor_dashboard_counts_patients(self):
		link_doctor_patient(self.doctor, self.patient)
		self.login(self.doctor)
		response = self.client.get(reverse('api:body_yar_doctor_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['data']['patient_count_total'], 1)

	def test_admin_dashboard_is_restricted_and_reports_core_counts(self):
		link_doctor_patient(self.doctor, self.patient)
		data = admin_dashboard(self.admin)
		self.assertEqual(data['user_count'], 3)
		self.assertEqual(data['doctor_count'], 1)
		with self.assertRaises(PermissionError):
			admin_dashboard(self.patient)

	def test_assign_patient_endpoint_links_by_national_code(self):
		self.login(self.doctor)
		response = self.client.post(reverse('api:body_yar_assign_patient'), data={'national_code': self.patient.national_code}, content_type='application/json')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(doctor_patient.objects.count(), 1)

	def test_prescribe_endpoint_creates_program(self):
		template = program_template.objects.create(title='قالب', doctor=self.doctor)
		program_template_item.objects.create(template=template, exercise=self.exercise)
		self.login(self.doctor)
		response = self.client.post(reverse('api:body_yar_prescribe_program'), data={'national_code': self.patient.national_code, 'template_id': template.pk}, content_type='application/json')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(patient_program.objects.count(), 1)

	def test_appointment_booking_requires_doctor_patient_link_and_availability(self):
		link_doctor_patient(self.doctor, self.patient)
		doctor_availability.objects.create(doctor=self.doctor, weekday=0, start_time=time(9, 0), end_time=time(12, 0), slot_minutes=30)
		starts_at = timezone.make_aware(datetime(2026, 9, 12, 9, 30))
		appointment_obj = book_appointment(self.patient, self.doctor, starts_at)
		self.assertEqual(appointment_obj.status, 'pending')
		with self.assertRaisesMessage(ValueError, 'قبلاً رزرو'):
			book_appointment(self.patient, self.doctor, starts_at)

	def test_subscription_endpoint_rejects_unauthenticated_user(self):
		pkg = package.objects.create(title='پکیج', is_published=True)
		plan = package_plan.objects.create(package=pkg, title='پلن', duration_months=1, price=100000)
		response = self.client.post(reverse('api:body_yar_subscription_create'), data={'plan_id': plan.pk}, content_type='application/json')
		self.assertEqual(response.status_code, 401)

	def test_patient_scope_excludes_other_patients(self):
		other = user_profile.objects.create_user(email='other@example.com', password='Secret123!', role=self.patient_role, phone='09120000003', national_code='0098765432')
		patient_program.objects.create(patient=self.patient, doctor=self.doctor, title='خودم')
		patient_program.objects.create(patient=other, doctor=self.doctor, title='دیگری')
		request = type('Request', (), {'user': self.patient})()
		queryset = scope_queryset(patient_program, request, patient_program.objects.all())
		self.assertEqual(list(queryset.values_list('title', flat=True)), ['خودم'])

	def test_doctor_scope_excludes_other_doctors(self):
		other_doctor = user_profile.objects.create_user(email='other-doctor@example.com', password='Secret123!', role=self.doctor_role, phone='09120000004')
		patient_program.objects.create(patient=self.patient, doctor=self.doctor, title='پزشک من')
		patient_program.objects.create(patient=self.patient, doctor=other_doctor, title='پزشک دیگر')
		request = type('Request', (), {'user': self.doctor})()
		queryset = scope_queryset(patient_program, request, patient_program.objects.all())
		self.assertEqual(list(queryset.values_list('title', flat=True)), ['پزشک من'])

	def test_content_models_remain_available_for_public_content_flow(self):
		article.objects.create(title='خبر', slug='news', body='متن')
		course.objects.create(title='دوره', slug='course')
		self.assertEqual(article.objects.count(), 1)
		self.assertEqual(course.objects.count(), 1)
