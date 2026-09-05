from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", core_views.landing, name="landing"),
    path("health/", core_views.health, name="health"),
    path("service-worker.js", core_views.service_worker, name="service-worker"),
    path("accounts/", include("apps.accounts.urls")),
    path("school/", include("apps.schools.urls")),
    path("planning/", include("apps.planning.urls")),
    path("curriculum/", include("apps.curriculum.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("api/", include("apps.api.urls")),
]

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
