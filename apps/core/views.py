from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    return render(request, "landing.html")


@require_GET
@never_cache
def health(request):
    status = "ok"
    code = 200
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        status = "unavailable"
        code = 503
    return JsonResponse({"status": status, "service": "cambrifyiq"}, status=code)


@require_GET
def service_worker(request):
    content = (Path(settings.BASE_DIR) / "static" / "js" / "service-worker.js").read_text()
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)
