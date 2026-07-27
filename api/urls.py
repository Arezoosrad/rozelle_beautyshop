"""
urls.py - مسیریابی API مدل‌محور
"""

from django.urls import path
from . import body_yar_views
from . import auth_views
from . import campaign_views
from . import consultation_views
from . import tracking_views
from . import views
from .body_yar_access import install as install_body_yar_access
from notifications import views as notification_views

install_body_yar_access()

app_name = 'api'

urlpatterns = [
    path('body-yar/dashboard/admin/', body_yar_views.admin_dashboard_view, name='body_yar_admin_dashboard'),
    path('body-yar/dashboard/doctor/', body_yar_views.doctor_dashboard_view, name='body_yar_doctor_dashboard'),
    path('body-yar/dashboard/patient/', body_yar_views.patient_dashboard_view, name='body_yar_patient_dashboard'),
    path('body-yar/store/packages/', tracking_views.package_store_view, name='body_yar_package_store'),
    path('body-yar/patients/assign/', body_yar_views.assign_patient_view, name='body_yar_assign_patient'),
    path('body-yar/programs/prescribe/', body_yar_views.prescribe_program_view, name='body_yar_prescribe_program'),
    path('body-yar/programs/<int:program_id>/ready/', body_yar_views.ready_program_view, name='body_yar_program_ready'),
    path('body-yar/assessments/<int:assessment_id>/complete/', body_yar_views.assessment_complete_view, name='body_yar_assessment_complete'),
    path('body-yar/subscriptions/create/', body_yar_views.subscription_create_view, name='body_yar_subscription_create'),
    path('body-yar/appointments/create/', body_yar_views.appointment_create_view, name='body_yar_appointment_create'),
    path('body-yar/messages/send/', body_yar_views.message_send_view, name='body_yar_message_send'),
    path('body-yar/exercises/execute/', tracking_views.exercise_execute_view, name='body_yar_exercise_execute'),
    path('body-yar/programs/<int:program_id>/progress/', tracking_views.exercise_progress_view, name='body_yar_exercise_progress'),
    path('body-yar/courses/enroll/', tracking_views.course_enroll_view, name='body_yar_course_enroll'),
    path('body-yar/courses/<int:enrollment_id>/progress/', tracking_views.lesson_progress_view, name='body_yar_lesson_progress'),
    path('body-yar/assessments/<int:assessment_id>/report/', tracking_views.assessment_report_view, name='body_yar_assessment_report'),
    path('body-yar/recommendations/packages/', tracking_views.package_recommendations_view, name='body_yar_package_recommendations'),
    path('body-yar/payments/create/', tracking_views.payment_create_view, name='body_yar_payment_create'),
    path('body-yar/payments/callback/<str:token>/', tracking_views.payment_callback_view, name='body_yar_payment_callback'),
    path('body-yar/subscriptions/<int:subscription_id>/cancel/', tracking_views.subscription_cancel_view, name='body_yar_subscription_cancel'),
    path('body-yar/subscriptions/<int:subscription_id>/renew/', tracking_views.subscription_renew_view, name='body_yar_subscription_renew'),
    path('body-yar/subscriptions/expire/', tracking_views.subscription_expiry_view, name='body_yar_subscription_expiry'),
    path('body-yar/reminders/dispatch/', tracking_views.reminder_dispatch_view, name='body_yar_reminder_dispatch'),
    path('body-yar/notifications/campaigns/<int:campaign_id>/dispatch/', campaign_views.campaign_dispatch_view, name='body_yar_campaign_dispatch'),
    path('body-yar/treatment-codes/create/', consultation_views.issue_treatment_code_view, name='body_yar_treatment_code_create'),
    path('body-yar/treatment-codes/redeem/', consultation_views.redeem_treatment_code_view, name='body_yar_treatment_code_redeem'),
    path('body-yar/consultations/request/', consultation_views.request_online_consultation_view, name='body_yar_consultation_request'),
    path('body-yar/consultations/<int:consultation_id>/complete/', consultation_views.complete_online_consultation_view, name='body_yar_consultation_complete'),
    path('notifications/push/key/', notification_views.push_public_key, name='push_public_key'),
    path('notifications/push/subscribe/', notification_views.subscribe_push, name='push_subscribe'),
    path('notifications/push/unsubscribe/', notification_views.unsubscribe_push, name='push_unsubscribe'),
    path('auth/help/', auth_views.auth_help, name='auth_help'),
    path('auth/check-phone/', auth_views.check_phone, name='check_phone'),
    path('auth/check-identifier/', auth_views.check_identifier, name='check_identifier'),
    path('auth/request-otp/', auth_views.request_otp, name='request_otp'),
    path('auth/login-password/', auth_views.login_with_password, name='login_password'),
    path('auth/verify-otp/', auth_views.verify_otp, name='verify_otp'),
    path('auth/verify-email/', auth_views.verify_email, name='verify_email'),
    path('auth/register/', auth_views.register, name='register'),
    path('auth/password-reset/request/', auth_views.request_password_reset, name='request_password_reset'),
    path('auth/password-reset/confirm/', auth_views.confirm_password_reset, name='confirm_password_reset'),
    path('auth/2fa/request/', auth_views.request_two_factor_setup, name='request_two_factor_setup'),
    path('auth/2fa/confirm/', auth_views.confirm_two_factor_setup, name='confirm_two_factor_setup'),
    path('auth/2fa/disable/', auth_views.disable_two_factor, name='disable_two_factor'),
    path('auth/logout/', auth_views.logout, name='logout'),
    path('help/', views.api_help, name='api_help'),
    path('<str:model_name>/help/', views.model_help, name='model_help'),
    path('<str:model_name>/<str:lookup>/help/', views.object_help, name='object_help'),
    path('<str:model_name>/<str:lookup>/call/<str:method_name>/', views.call_model_method, name='call_model_method'),
    path('<str:model_name>/', views.model_collection, name='model_collection'),
    path('<str:model_name>/<str:lookup>/', views.model_detail, name='model_detail'),
]
