from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.dashboard import reporting
from apps.plans.models import LessonPlan, PlanState, WorkPlan
from apps.plans.services import visible_plans
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

    is_teacher = membership.role == Membership.Role.TEACHER
    analytics = reporting.dashboard_context(membership)

    recent_plans = list(visible_plans(membership, LessonPlan)[:5])
    action_needed = list(visible_plans(membership, LessonPlan).filter(state=PlanState.RETURNED)[:5])

    context = {
        "stats": stats,
        "assignments": active_assignments[:6],
        "setup": setup,
        "setup_percent": round(sum(setup.values()) / len(setup) * 100),
        "is_leader": membership.can_manage_users,
        "is_teacher": is_teacher,
        "is_coordinator": membership.role == Membership.Role.COORDINATOR,
        "analytics": analytics,
        "summary": analytics.get("summary"),
        "pending": analytics.get("pending"),
        "coverage": analytics.get("coverage", [])[:6],
        "content": analytics.get("content"),
        "completion": analytics.get("completion"),
        "turnaround": analytics.get("turnaround"),
        "documents": analytics.get("documents"),
        "recent_plans": recent_plans,
        "action_needed": action_needed,
        "work_plan_total": visible_plans(membership, WorkPlan).count(),
    }
    return render(request, "dashboard/home.html", context)
