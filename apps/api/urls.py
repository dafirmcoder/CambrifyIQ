from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    path("auth/login/", views.LoginAPIView.as_view(), name="login"),
    path("auth/logout/", views.LogoutAPIView.as_view(), name="logout"),
    path("me/", views.MeAPIView.as_view(), name="me"),
    path("me/assignments/", views.MyAssignmentsAPIView.as_view(), name="assignments"),
]
