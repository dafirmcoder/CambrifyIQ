from django.urls import path

from apps.api import curriculum_views, views

app_name = "api"

urlpatterns = [
    path("auth/login/", views.LoginAPIView.as_view(), name="login"),
    path("auth/logout/", views.LogoutAPIView.as_view(), name="logout"),
    path("me/", views.MeAPIView.as_view(), name="me"),
    path("me/assignments/", views.MyAssignmentsAPIView.as_view(), name="assignments"),
    path("schemes/", curriculum_views.SchemeListAPIView.as_view(), name="schemes"),
    path(
        "schemes/<uuid:scheme_id>/objectives/",
        curriculum_views.SchemeObjectiveAPIView.as_view(),
        name="scheme-objectives",
    ),
    path("templates/", curriculum_views.TemplateAPIView.as_view(), name="templates"),
    path(
        "templates/<uuid:version_id>/fields/",
        curriculum_views.TemplateFieldAPIView.as_view(),
        name="template-fields",
    ),
]
