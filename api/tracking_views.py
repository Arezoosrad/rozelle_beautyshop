import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from tracking.package_service import browse_packages
from tracking.report_service import generate_assessment_report
from tracking.services import PaymentGatewayError, create_payment_request, dispatch_due_reminders, enroll_course, exercise_progress, record_exercise_execution, recommend_packages, update_course_progress, verify_payment_callback
from tracking.subscription_service import cancel_subscription, expire_subscriptions, renew_subscription, set_subscription_expiry


def _json(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _error(code, message, status=400):
    return JsonResponse({'ok': False, 'error': {'code': code, 'message': message}}, status=status, json_dumps_params={'ensure_ascii': False})


def _auth(request):
    if not getattr(request.user, 'is_authenticated', False):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    return None


def _run(request, callback):
    auth = _auth(request)
    if auth:
        return auth
    try:
        return JsonResponse({'ok': True, 'data': callback()}, json_dumps_params={'ensure_ascii': False})
    except PermissionError as exc:
        return _error('permission_denied', str(exc), 403)
    except (ValueError, LookupError, PaymentGatewayError) as exc:
        return _error('invalid_request', str(exc), 400)
    except Exception:
        return _error('server_error', 'اجرای عملیات با خطای داخلی مواجه شد.', 500)


@require_http_methods(['GET'])
def package_store_view(request):
    items = browse_packages(request.GET.get('q', ''), request.GET.get('category', ''), request.GET.get('target_area', ''), request.GET.get('tag_id') or None)
    return JsonResponse({'ok': True, 'data': [{'id': item.pk, 'title': item.title, 'description': item.description, 'target_area': item.target_area, 'category': item.category} for item in items]}, json_dumps_params={'ensure_ascii': False})


@require_http_methods(['POST'])
def exercise_execute_view(request):
    payload = _json(request)
    if payload is None or not payload.get('program_item_id') or not payload.get('execution_date'):
        return _error('invalid_payload', 'program_item_id و execution_date الزامی هستند.')
    return _run(request, lambda: {'id': record_exercise_execution(request.user, payload['program_item_id'], payload['execution_date'], payload.get('status', 'completed'), payload.get('actual_sets', 0), payload.get('actual_repetitions', 0), payload.get('actual_duration_seconds', 0), payload.get('pain_level', 0), payload.get('notes', ''), payload.get('not_done_reason', '')).pk})


@require_http_methods(['GET'])
def exercise_progress_view(request, program_id):
    return _run(request, lambda: exercise_progress(request.user, program_id))


@require_http_methods(['POST'])
def course_enroll_view(request):
    payload = _json(request)
    if payload is None or not payload.get('course_id'):
        return _error('missing_course', 'course_id الزامی است.')
    return _run(request, lambda: {'id': enroll_course(request.user, payload['course_id']).pk})


@require_http_methods(['POST'])
def lesson_progress_view(request, enrollment_id):
    payload = _json(request)
    if payload is None or not payload.get('lesson_id'):
        return _error('missing_lesson', 'lesson_id الزامی است.')
    return _run(request, lambda: {'id': update_course_progress(request.user, enrollment_id, payload['lesson_id'], payload.get('progress_percent', 0), payload.get('position_seconds', 0)).pk})


@require_http_methods(['POST'])
def assessment_report_view(request, assessment_id):
    return _run(request, lambda: {'id': generate_assessment_report(request.user, assessment_id).pk})


@require_http_methods(['GET'])
def package_recommendations_view(request):
    diagnosis_id = request.GET.get('diagnosis_id')
    return _run(request, lambda: [{'id': item.pk, 'package_id': item.package_id, 'score': str(item.score), 'reason': item.reason} for item in recommend_packages(request.user, diagnosis_id)])


@require_http_methods(['POST'])
def payment_create_view(request):
    payload = _json(request)
    if payload is None or not payload.get('subscription_id') or not payload.get('request_key'):
        return _error('invalid_payload', 'subscription_id و request_key الزامی هستند.')
    return _run(request, lambda: _payment_response(request.user, payload['subscription_id'], payload['request_key']))


def _payment_response(user, subscription_id, request_key):
    request, redirect_url = create_payment_request(user, subscription_id, request_key)
    return {'request_id': request.pk, 'redirect_url': redirect_url}


@require_http_methods(['GET'])
def payment_callback_view(request, token):
    try:
        result = verify_payment_callback(token, request.GET.get('trackId') or request.GET.get('authority', ''), request.GET.get('success') in ('1', 'true', 'True'))
        if result.status == 'verified':
            set_subscription_expiry(result.subscription)
        return JsonResponse({'ok': result.status == 'verified', 'status': result.status})
    except Exception:
        return JsonResponse({'ok': False, 'status': 'failed'}, status=400)


@require_http_methods(['POST'])
def subscription_cancel_view(request, subscription_id):
    return _run(request, lambda: {'id': cancel_subscription(request.user, subscription_id).pk, 'status': 'cancelled'})


@require_http_methods(['POST'])
def subscription_renew_view(request, subscription_id):
    payload = _json(request)
    if payload is None or not payload.get('request_key'):
        return _error('missing_request_key', 'request_key الزامی است.')
    return _run(request, lambda: _renew_response(request.user, subscription_id, payload['request_key']))


def _renew_response(user, subscription_id, request_key):
    request, redirect_url = renew_subscription(user, subscription_id, request_key)
    return {'request_id': request.pk, 'redirect_url': redirect_url}


@require_http_methods(['POST'])
def reminder_dispatch_view(request):
    auth = _auth(request)
    if auth:
        return auth
    if not getattr(request.user, 'is_superuser', False) and getattr(getattr(request.user, 'role', None), 'role_type', None) != 'admin':
        return _error('permission_denied', 'دسترسی مدیر مورد نیاز است.', 403)
    return JsonResponse({'ok': True, 'data': {'sent': dispatch_due_reminders()}})


@require_http_methods(['POST'])
def subscription_expiry_view(request):
    auth = _auth(request)
    if auth:
        return auth
    if not getattr(request.user, 'is_superuser', False) and getattr(getattr(request.user, 'role', None), 'role_type', None) != 'admin':
        return _error('permission_denied', 'دسترسی مدیر مورد نیاز است.', 403)
    return JsonResponse({'ok': True, 'data': {'expired': expire_subscriptions()}})
