from django.urls import path

from apps.schools import views

app_name = "schools"

urlpatterns = [
    # ── Existing ──────────────────────────────────────────────────────────────
    path("settings/", views.school_settings, name="settings"),
    path("team/", views.team, name="team"),
    path("team/invite/", views.invite_member, name="invite"),
    path("team/<uuid:membership_id>/update/", views.update_member_view, name="update_member"),
    path("switch/<uuid:school_id>/", views.switch_school, name="switch"),
    path("invitations/<str:token>/accept/", views.accept_invitation_view, name="accept_invitation"),
    # ── Academic years ────────────────────────────────────────────────────────
    path("years/", views.academic_years, name="academic_years"),
    path("years/<uuid:year_id>/edit/", views.edit_academic_year, name="edit_academic_year"),
    path("years/<uuid:year_id>/delete/", views.delete_academic_year, name="delete_academic_year"),
    # ── Terms ─────────────────────────────────────────────────────────────────
    path("years/<uuid:year_id>/terms/", views.terms, name="terms"),
    path("years/<uuid:year_id>/terms/<uuid:term_id>/edit/", views.edit_term, name="edit_term"),
    path(
        "years/<uuid:year_id>/terms/<uuid:term_id>/delete/",
        views.delete_term,
        name="delete_term",
    ),
    # ── Calendar ──────────────────────────────────────────────────────────────
    path(
        "years/<uuid:year_id>/terms/<uuid:term_id>/calendar/",
        views.calendar_weeks,
        name="calendar_weeks",
    ),
    # ── Subjects ──────────────────────────────────────────────────────────────
    path("subjects/", views.subjects, name="subjects"),
    path("subjects/<uuid:subject_id>/edit/", views.edit_subject, name="edit_subject"),
    path("subjects/<uuid:subject_id>/delete/", views.delete_subject, name="delete_subject"),
    # ── Classes ───────────────────────────────────────────────────────────────
    path("classes/", views.school_classes, name="school_classes"),
    path("classes/<uuid:class_id>/edit/", views.edit_school_class, name="edit_school_class"),
    path("classes/<uuid:class_id>/delete/", views.delete_school_class, name="delete_school_class"),
    # ── Teaching assignments ──────────────────────────────────────────────────
    path("assignments/", views.teaching_assignments, name="teaching_assignments"),
    path("assignments/create/", views.create_assignment, name="create_assignment"),
    path(
        "assignments/<uuid:assignment_id>/edit/",
        views.edit_assignment,
        name="edit_assignment",
    ),
    path(
        "assignments/<uuid:assignment_id>/delete/",
        views.delete_assignment,
        name="delete_assignment",
    ),
]
