from django.conf import settings
from rozelle_beautyshop.core.melipayamak import Api


def send_sms(to: str, text: str, body_id: int) -> bool:
    """
    Send SMS via Melipayamak base-number (pattern) API.

    Args:
        to: Recipient phone number
        text: Semicolon-separated values matching the pattern placeholders
        body_id: Pattern (body) ID registered in Melipayamak panel

    Returns:
        True if the API accepted the request, False otherwise
    """
    username = settings.MELIPAYAMAK_USERNAME
    password = settings.MELIPAYAMAK_PASSWORD
    api = Api(username, password)
    sms = api.sms()
    response = sms.send_by_base_number(text, to, body_id)
    try:
        return int(response.get('Value', 0)) > 0
    except (TypeError, ValueError):
        return False
