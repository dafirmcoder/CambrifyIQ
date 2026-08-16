"""Leadership reporting and coverage analytics (plan sections 7.6 and 13.5).

Each role gets a different lens on the same tenant-scoped data:

* Teacher    — own assignments, drafts, returned plans and recent documents.
* Coordinator— content health, pending reviews and coverage by subject/class.
* Head       — compliance, coverage versus scheme and overdue approvals.
* Director   — whole-school KPIs, completion and turnaround.
"""

from datetime import timedelta

from django.db.models import Avg, Count, F, Q
from django.utils import timezone

from apps.curriculum.models import LearningObjective, SchemeOfWork
from apps.plans.models import (
    PENDING_STATES,
    GeneratedDocument,
    LessonPlan,
    PlanState,
    WorkPlan,
)
from apps.schools.models import Membership, TeacherAssignment

#: A submitted plan older than this is treated as an overdue approval (7.6).
OVERDUE_AFTER_DAYS = 7


def _scoped(model, membership):
    queryset = model.objects.for_school(membership.school_id)
    if membership.role == Membership.Role.TEACHER:
        return queryset.filter(author_id=membership.user_id)
    return queryset


def plan_state_counts(membership, model):
    """Plan totals per workflow state."""
    rows = _scoped(model, membership).values("state").annotate(total=Count("id"))
    counts = {state: 0 for state, _ in PlanState.choices}
    for row in rows:
        counts[row["state"]] = row["total"]
    return counts


def teacher_summary(membership):
    """Teacher dashboard figures (7.6)."""
    lessons = _scoped(LessonPlan, membership)
    works = _scoped(WorkPlan, membership)
    today = timezone.localdate()
    return {
        "assignments": TeacherAssignment.objects.for_school(membership.school_id)
        .filter(teacher_id=membership.user_id, is_active=True, effective_from__lte=today)
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=today))
        .count(),
        "drafts": lessons.filter(state=PlanState.DRAFT).count()
        + works.filter(state=PlanState.DRAFT).count(),
        "returned": lessons.filter(state=PlanState.RETURNED).count()
        + works.filter(state=PlanState.RETURNED).count(),
        "approved": lessons.filter(state=PlanState.APPROVED).count()
        + works.filter(state=PlanState.APPROVED).count(),
        "awaiting": lessons.filter(state__in=PENDING_STATES).count()
        + works.filter(state__in=PENDING_STATES).count(),
    }


def pending_review_counts(membership):
    """How much is sitting in the approval queue, and how much is overdue."""
    cutoff = timezone.now() - timedelta(days=OVERDUE_AFTER_DAYS)
    result = {}
    for key, model in (("lesson_plans", LessonPlan), ("work_plans", WorkPlan)):
        queryset = (
            model.objects.for_school(membership.school_id)
            .filter(state__in=PENDING_STATES)
            .exclude(author_id=membership.user_id)
        )
        result[key] = queryset.count()
        result[f"{key}_overdue"] = queryset.filter(submitted_at__lt=cutoff).count()
    result["total"] = result["lesson_plans"] + result["work_plans"]
    result["overdue"] = result["lesson_plans_overdue"] + result["work_plans_overdue"]
    return result


def coverage_by_assignment(membership, limit=25):
    """Curriculum coverage per subject and class (7.6, 13.3).

    Coverage compares the objectives a teacher has actually planned against the
    objectives published in the matching scheme of work.
    """
    assignments = (
        TeacherAssignment.objects.for_school(membership.school_id)
        .filter(is_active=True)
        .select_related("subject", "school_class", "teacher")
    )
    if membership.role == Membership.Role.TEACHER:
        assignments = assignments.filter(teacher_id=membership.user_id)

    rows = []
    for assignment in assignments[:limit]:
        scheme_ids = (
            SchemeOfWork.objects.for_school(membership.school_id)
            .filter(
                subject_id=assignment.subject_id,
                school_class_id=assignment.school_class_id,
                status=SchemeOfWork.Status.PUBLISHED,
            )
            .values_list("pk", flat=True)
        )

        available = (
            LearningObjective.objects.for_school(membership.school_id)
            .filter(topic__scheme_id__in=list(scheme_ids), is_active=True)
            .count()
        )
        planned = (
            LearningObjective.objects.for_school(membership.school_id)
            .filter(
                topic__scheme_id__in=list(scheme_ids),
                work_plan_rows__work_plan__assignment_id=assignment.pk,
            )
            .distinct()
            .count()
        )
        percent = round(planned / available * 100) if available else 0
        rows.append(
            {
                "assignment": assignment,
                "subject": assignment.subject.name,
                "class_name": assignment.school_class.name,
                "teacher": assignment.teacher.get_short_name(),
                "available": available,
                "planned": planned,
                "percent": percent,
            }
        )
    rows.sort(key=lambda row: row["percent"])
    return rows


def turnaround_days(membership):
    """Average days from submission to approval, a Director KPI (7.6)."""
    values = []
    for model in (LessonPlan, WorkPlan):
        aggregate = (
            model.objects.for_school(membership.school_id)
            .filter(state=PlanState.APPROVED, submitted_at__isnull=False)
            .annotate(gap=F("approved_at") - F("submitted_at"))
            .aggregate(average=Avg("gap"))
        )
        if aggregate["average"]:
            values.append(aggregate["average"].total_seconds() / 86400)
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def completion_rate(membership):
    """Share of all plans that reached approval."""
    total = 0
    approved = 0
    for model in (LessonPlan, WorkPlan):
        queryset = model.objects.for_school(membership.school_id)
        total += queryset.count()
        approved += queryset.filter(state__in=(PlanState.APPROVED, PlanState.ARCHIVED)).count()
    return round(approved / total * 100) if total else 0


def content_health(membership):
    """Coordinator view of curriculum data quality (7.6)."""
    schemes = SchemeOfWork.objects.for_school(membership.school_id)
    objectives = LearningObjective.objects.for_school(membership.school_id)
    published = schemes.filter(status=SchemeOfWork.Status.PUBLISHED)
    empty = published.annotate(topic_total=Count("topics")).filter(topic_total=0).count()
    return {
        "schemes": schemes.count(),
        "published": published.count(),
        "drafts": schemes.filter(status=SchemeOfWork.Status.DRAFT).count(),
        "objectives": objectives.filter(is_active=True).count(),
        "schemes_without_topics": empty,
    }


def recent_documents(membership, limit=8):
    queryset = GeneratedDocument.objects.for_school(membership.school_id)
    if membership.role == Membership.Role.TEACHER:
        own_lessons = list(
            LessonPlan.objects.for_school(membership.school_id)
            .filter(author_id=membership.user_id)
            .values_list("pk", flat=True)
        )
        own_works = list(
            WorkPlan.objects.for_school(membership.school_id)
            .filter(author_id=membership.user_id)
            .values_list("pk", flat=True)
        )
        queryset = queryset.filter(plan_id__in=own_lessons + own_works)
    return queryset[:limit]


def dashboard_context(membership):
    """Everything the role dashboard needs, in one call."""
    role = membership.role
    context = {
        "role": role,
        "lesson_states": plan_state_counts(membership, LessonPlan),
        "work_states": plan_state_counts(membership, WorkPlan),
        "documents": recent_documents(membership),
    }

    if role == Membership.Role.TEACHER:
        context["summary"] = teacher_summary(membership)
        context["coverage"] = coverage_by_assignment(membership)
        return context

    context["pending"] = pending_review_counts(membership)
    context["coverage"] = coverage_by_assignment(membership)
    context["completion"] = completion_rate(membership)
    context["turnaround"] = turnaround_days(membership)

    if role == Membership.Role.COORDINATOR:
        context["content"] = content_health(membership)
    return context
