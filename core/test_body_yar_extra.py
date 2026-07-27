import json
from datetime import datetime, time

from django.test import TestCase
from django.utils import timezone

from appointments.models import appointment, doctor_availability
from assessments.models import patient_assessment, question_option, questionnaire, questionnaire_question
from clinical.models import doctor_profile, patient_profile
from profiles.models import user_profile, user_role
from .body_yar import book_appointment, complete_assessment


class BodyYarExtendedFlowTestCase(TestCase):
	def setUp(self):
		self.doctor_role = user_role.objects.create(name='doctor-extra', name_fa='پزشک', role_type='expert', level=20)
		self.patient_role = user_role.objects.create(name='patient-extra', name_fa='بیمار', role_type='customer', level=50)
		self.doctor = user_profile.objects.create_user(email='doctor-extra@example.com', password='Secret123!', role=self.doctor_role)
		self.patient = user_profile.objects.create_user(email='patient-extra@example.com', password='Secret123!', role=self.patient_role)
		doctor_profile.objects.create(user=self.doctor, code='DR-EXTRA', specialty='فیزیوتراپی', is_verified=True)
		patient_profile.objects.create(user=self.patient)

	def test_multiple_choice_is_serialized_and_scored(self):
		questionnaire_obj = questionnaire.objects.create(title='چندگزینه‌ای', assessment_type='general')
		question = questionnaire_question.objects.create(questionnaire=questionnaire_obj, text='علائم', question_type='multiple', is_required=True, score_enabled=True)
		first = question_option.objects.create(question=question, label='درد', value='pain', score=2)
		second = question_option.objects.create(question=question, label='خشکی', value='stiffness', score=3)
		assessment = patient_assessment.objects.create(patient=self.patient, questionnaire=questionnaire_obj)
		complete_assessment(self.patient, assessment.pk, {str(question.pk): [first.pk, second.pk]})
		answer = assessment.answers.get(question=question)
		self.assertEqual(json.loads(answer.answer_text), [first.pk, second.pk])
		assessment.refresh_from_db()
		self.assertEqual(assessment.total_score, 5)

	def test_appointment_booking_rejects_unassigned_doctor(self):
		starts_at = timezone.make_aware(datetime(2026, 9, 12, 9, 30))
		with self.assertRaisesMessage(PermissionError, 'اختصاص داده نشده'):
			book_appointment(self.patient, self.doctor, starts_at)

	def test_appointment_booking_creates_pending_slot(self):
		from clinical.models import doctor_patient
		doctor_patient.objects.create(doctor=self.doctor.doctor_profile, patient=self.patient.patient_profile)
		doctor_availability.objects.create(doctor=self.doctor, weekday=0, start_time=time(9, 0), end_time=time(12, 0), slot_minutes=30)
		starts_at = timezone.make_aware(datetime(2026, 9, 12, 9, 30))
		created = book_appointment(self.patient, self.doctor, starts_at, 'یادداشت')
		self.assertEqual(created.status, 'pending')
		self.assertEqual(appointment.objects.count(), 1)
