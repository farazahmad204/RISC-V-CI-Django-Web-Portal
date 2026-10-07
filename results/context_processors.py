from django.conf import settings


def portal(request):
    """Expose the portal's display name to every template."""
    return {"portal_name": settings.PORTAL_NAME}
