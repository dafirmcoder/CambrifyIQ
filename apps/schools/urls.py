from django.urls import path

from apps.schools import views

app_name = "schools"

urlpatterns = [
    path("settings/", views.school_settings, name="settings"),
    path("team/", views.team, name="team"),
    path("team/invite/", views.invite_member, name="invite"),
    path("team/<uuid:membership_id>/update/", views.update_member_view, name="update_member"),
    path("switch/<uuid:school_id>/", views.switch_school, name="switch"),
    path("invitations/<str:token>/accept/", views.accept_invitation_view, name="accept_invitation"),
]
