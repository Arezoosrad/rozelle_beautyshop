import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from consultations.models import online_consultation
from consultations.services import complete_online_consultation, issue_treatment_code, redeem_treatment_code, request_online_consultation


def _payload(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _auth(request):
    return getattr(request.user, 'is_authenticated', False)


def _error(code, message, status=400):
    return JsonResponse({'ok': False, 'error': {'code': code, 'message': message}}, status=status, json_dumps_params={'ensure_ascii': False})


@require_http_methods(['POST'])
def issue_treatment_code_view(request):
    if not _auth(request):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    payload = _payload(request)
    if payload is None or not payload.get('code'):
        return _error('missing_code', 'code الزامی است.')
    try:
        obj = issue_treatment_code(request.user, payload['code'], notes=payload.get('notes', ''))
        return JsonResponse({'ok': True, 'data': {'id': obj.pk, 'code': obj.code}}, json_dumps_params={'ensure_ascii': False})
    except Exception as exc:
        return _error('invalid_request', str(exc), 400)


@require_http_methods(['POST'])
def redeem_treatment_code_view(request):
    if not _auth(request):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    payload = _payload(request)
    if payload is None or not payload.get('code'):
        return _error('missing_code', 'code الزامی است.')
    try:
        obj = redeem_treatment_code(request.user, payload['code'])
        return JsonResponse({'ok': True, 'data': {'id': obj.pk, 'doctor_id': obj.doctor_id, 'status': obj.status}}, json_dumps_params={'ensure_ascii': False})
    except PermissionError as exc:
        return _error('permission_denied', str(exc), 403)
    except Exception as exc:
        return _error('invalid_request', str(exc), 400)


@require_http_methods(['POST'])
def request_online_consultation_view(request):
    if not _auth(request):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    payload = _payload(request)
    if payload is None or not payload.get('doctor_id') or not payload.get('problem_description'):
        return _error('missing_consultation_data', 'doctor_id و problem_description الزامی هستند.')
    try:
        obj = request_online_consultation(request.user, payload['doctor_id'], payload['problem_description'], payload.get('treatment_code', ''))
        return JsonResponse({'ok': True, 'data': {'id': obj.pk, 'status': obj.status}}, json_dumps_params={'ensure_ascii': False})
    except PermissionError as exc:
        return _error('permission_denied', str(exc), 403)
    except Exception as exc:
        return _error('invalid_request', str(exc), 400)


@require_http_methods(['POST'])
def complete_online_consultation_view(request, consultation_id):
    if not _auth(request):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    payload = _payload(request)
    if payload is None:
        return _error('invalid_json', 'بدنه درخواست JSON معتبر نیست.')
    try:
        result = complete_online_consultation(request.user, consultation_id, payload.get('notes', ''))
        return JsonResponse({'ok': True, 'data': result}, json_dumps_params={'ensure_ascii': False})
    except PermissionError as exc:
        return _error('permission_denied', str(exc), 403)
    except Exception as exc:
        return _error('invalid_request', str(exc), 400)
