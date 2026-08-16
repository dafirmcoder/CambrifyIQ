from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.schools.models import (
    AcademicYear,
    Invitation,
    Membership,
    SchoolClass,
    Subject,
    TeacherAssignment,
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

    stats = {
        "assignments": active_assignments.count(),
        "team_members": Membership.objects.filter(
            school=request.school, status=Membership.Status.ACTIVE
        ).count(),
        "subjects": Subject.objects.filter(is_active=True).count(),
        "classes": SchoolClass.objects.filter(is_active=True).count(),
        "pending_invites": Invitation.objects.filter(status=Invitation.Status.PENDING).count(),
    }
    setup = {
        "school_profile": bool(
            request.school.address or request.school.phone or request.school.website
        ),
        "academic_year": AcademicYear.objects.exists(),
        "subjects": stats["subjects"] > 0,
        "classes": stats["classes"] > 0,
        "team": stats["team_members"] > 1,
    }
    context = {
        "stats": stats,
        "assignments": active_assignments[:6],
        "setup": setup,
        "setup_percent": round(sum(setup.values()) / len(setup) * 100),
        "is_leader": membership.can_manage_users,
        "is_teacher": membership.role == Membership.Role.TEACHER,
        "is_coordinator": membership.role == Membership.Role.COORDINATOR,
    }
    return render(request, "dashboard/home.html", context)
