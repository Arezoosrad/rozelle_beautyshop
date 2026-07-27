import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from tracking.campaign_service import dispatch_campaign


def _error(code, message, status=400):
    return JsonResponse({'ok': False, 'error': {'code': code, 'message': message}}, status=status, json_dumps_params={'ensure_ascii': False})

@require_http_methods(['POST'])
def campaign_dispatch_view(request, campaign_id):
    if not getattr(request.user, 'is_authenticated', False):
        return _error('authentication_required', 'ورود به سامانه الزامی است.', 401)
    if not getattr(request.user, 'is_superuser', False) and getattr(getattr(request.user, 'role', None), 'role_type', None) != 'admin':
        return _error('permission_denied', 'دسترسی مدیر مورد نیاز است.', 403)
    try:
        return JsonResponse({'ok': True, 'data': {'sent': dispatch_campaign(campaign_id)}})
    except Exception as exc:
        return _error('invalid_request', str(exc), 400)
