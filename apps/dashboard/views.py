from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.planning.models import LessonPlan, WorkPlan
from apps.schools.models import (
    AcademicYear,
    CalendarWeek,
    Invitation,
    Membership,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)


@login_required
def home(request):
    if not request.school or not request.membership:
        return redirect("accounts:create_school")

    today = timezone.localdate()
    membership = request.membership
    active_assignments = TeacherAssignment.objects.filter(
        teacher=request.user,
        is_active=True,
        effective_from__lte=today,
    ).filter(effective_until__isnull=True) | TeacherAssignment.objects.filter(
        teacher=request.user,
        is_active=True,
        effective_from__lte=today,
        effective_until__gte=today,
    )
    active_assignments = active_assignments.select_related("subject", "school_class").distinct()

    teacher_work_plans = WorkPlan.objects.filter(author=request.user)
    teacher_lesson_plans = LessonPlan.objects.filter(author=request.user)

    draft_plans_count = (
        teacher_work_plans.filter(status=WorkPlan.Status.DRAFT).count()
        + teacher_lesson_plans.filter(status=LessonPlan.Status.DRAFT).count()
    )
    returned_plans_count = (
        teacher_work_plans.filter(status=WorkPlan.Status.RETURNED).count()
        + teacher_lesson_plans.filter(status=LessonPlan.Status.RETURNED).count()
    )
    approved_plans_count = (
        teacher_work_plans.filter(status=WorkPlan.Status.APPROVED).count()
        + teacher_lesson_plans.filter(status=LessonPlan.Status.APPROVED).count()
    )
    submitted_plans_count = (
        teacher_work_plans.filter(
            status__in=[
                WorkPlan.Status.SUBMITTED,
                WorkPlan.Status.UNDER_REVIEW,
                WorkPlan.Status.RESUBMITTED,
            ]
        ).count()
        + teacher_lesson_plans.filter(
            status__in=[
                LessonPlan.Status.SUBMITTED,
                LessonPlan.Status.UNDER_REVIEW,
                LessonPlan.Status.RESUBMITTED,
            ]
        ).count()
    )

    stats = {
        "assignments": active_assignments.count(),
        "draft_plans": draft_plans_count,
        "returned_plans": returned_plans_count,
        "approved_plans": approved_plans_count,
        "submitted_plans": submitted_plans_count,
        "team_members": Membership.objects.filter(
            school=request.school, status=Membership.Status.ACTIVE
        ).count(),
        "subjects": Subject.objects.filter(is_active=True).count(),
        "classes": SchoolClass.objects.filter(is_active=True).count(),
        "pending_invites": Invitation.objects.filter(status=Invitation.Status.PENDING).count(),
    }

    planning_activity = (
        WorkPlan.objects.select_related(
            "assignment__subject", "assignment__school_class", "author", "term"
        ).order_by("-updated_at")[:6]
        if not (membership.role == Membership.Role.TEACHER)
        else []
    )

    has_year = AcademicYear.objects.exists()
    has_term = Term.objects.exists()
    has_weeks = CalendarWeek.objects.exists()
    has_assignments = TeacherAssignment.objects.filter(is_active=True).exists()

    setup = {
        "school_profile": bool(
            request.school.address or request.school.phone or request.school.website
        ),
        "academic_year": has_year,
        "term": has_term,
        "calendar_weeks": has_weeks,
        "subjects": stats["subjects"] > 0,
        "classes": stats["classes"] > 0,
        "assignments": has_assignments,
        "team": stats["team_members"] > 1,
    }

    # Deep-links for each checklist item (for leaders)
    setup_urls = {
        "school_profile": reverse("schools:settings"),
        "academic_year": reverse("schools:academic_years"),
        "term": reverse("schools:academic_years"),  # navigate into the year then add term
        "calendar_weeks": reverse("schools:academic_years"),
        "subjects": reverse("schools:subjects"),
        "classes": reverse("schools:school_classes"),
        "assignments": reverse("schools:teaching_assignments"),
        "team": reverse("schools:team"),
    }

    setup_items = [
        ("school_profile", "Complete school profile", 1),
        ("academic_year", "Add academic year", 2),
        ("term", "Add terms", 3),
        ("calendar_weeks", "Build teaching calendar", 4),
        ("subjects", "Add subjects", 5),
        ("classes", "Add classes", 6),
        ("assignments", "Create teaching assignments", 7),
        ("team", "Invite your team", 8),
    ]

    context = {
        "stats": stats,
        "assignments": active_assignments[:6],
        "planning_activity": planning_activity,
        "setup": setup,
        "setup_urls": setup_urls,
        "setup_items": setup_items,
        "setup_percent": round(sum(setup.values()) / len(setup) * 100),
        "is_leader": membership.can_manage_users,
        "is_teacher": membership.role == Membership.Role.TEACHER,
        "is_coordinator": membership.role == Membership.Role.COORDINATOR,
    }
    return render(request, "dashboard/home.html", context)
