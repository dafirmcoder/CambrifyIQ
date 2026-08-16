"""Plan creation and autosave services (plan sections 7.2, 7.3, 8.5).

Autosave uses revision tokens: a client sends the revision it read, and a stale
token raises ``RevisionConflict`` rather than silently overwriting a newer edit
(8.5, 10.3). Approved plans are immutable, so any write to one is refused.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.curriculum.services import resolve_selected_objectives
from apps.planning.models import PlanType
from apps.planning.services import current_version
from apps.plans.models import (
    LessonPlan,
    PlanState,
    WorkPlan,
    WorkPlanRow,
)
from apps.plans.validation import validate_attendance
from apps.plans.workflow import assert_is_author, plan_type_of
from apps.schools.calendar import generate_weeks
from apps.schools.models import AuditLog, CalendarWeek, Membership, TeacherAssignment


class RevisionConflict(ValidationError):
    """Raised when a save is based on a stale revision token."""


def _require_template(school, plan_type):
    version = current_version(school=school, plan_type=plan_type)
    if version is None:
        raise ValidationError(
            "No approved template version is published yet. Complete the template lockdown first."
        )
    return version


def resolve_assignment(membership, assignment_id, on_date=None):
    """Fetch an assignment that is genuinely the caller's and active."""
    on_date = on_date or timezone.localdate()
    assignment = (
        TeacherAssignment.objects.for_school(membership.school_id)
        .filter(pk=assignment_id, teacher_id=membership.user_id, is_active=True)
        .select_related("subject", "school_class")
        .first()
    )
    if assignment is None:
        raise PermissionDenied("That teaching assignment is not available to you.")
    if assignment.effective_from > on_date or (
        assignment.effective_until and assignment.effective_until < on_date
    ):
        raise PermissionDenied("That teaching assignment is not active.")
    return assignment


def _audit(membership, action, plan, **metadata):
    AuditLog.all_objects.create(
        school_id=plan.school_id,
        actor_id=membership.user_id,
        action=action,
        target_type=plan_type_of(plan),
        target_id=str(plan.pk),
        metadata={"revision": plan.revision, **metadata},
    )


def _guard_writable(plan):
    if plan.is_locked:
        raise ValidationError("An approved plan is immutable. Create a new revision instead.")
    if not plan.is_editable:
        raise ValidationError("This plan is awaiting review and cannot be edited.")


def _check_revision(plan, base_revision):
    if base_revision is not None and int(base_revision) != plan.revision:
        raise RevisionConflict(
            f"This plan was updated elsewhere (revision {plan.revision}). "
            "Reload before saving to avoid losing work."
        )


def _bump(plan, update_fields):
    plan.revision += 1
    plan.save(update_fields=[*update_fields, "revision", "updated_at"])
    return plan


@transaction.atomic
def create_work_plan(*, membership, assignment_id, term, scheme=None):
    """Open a Work Plan and generate its calendar week rows (7.2)."""
    assignment = resolve_assignment(membership, assignment_id)
    version = _require_template(membership.school, PlanType.WORK_PLAN)

    existing = WorkPlan.all_objects.filter(
        school_id=membership.school_id, assignment=assignment, term=term
    ).exclude(state=PlanState.ARCHIVED)
    if existing.exists():
        raise ValidationError("A work plan already exists for this assignment and term.")

    plan = WorkPlan.all_objects.create(
        school_id=membership.school_id,
        template_version=version,
        assignment=assignment,
        author_id=membership.user_id,
        academic_year=term.academic_year,
        term=term,
        scheme=scheme,
    )

    weeks = generate_weeks(term)
    WorkPlanRow.all_objects.bulk_create(
        [
            WorkPlanRow(
                school_id=plan.school_id,
                work_plan=plan,
                calendar_week=week,
                week_number=week.number,
                month_label=week.month_label,
                week_label=week.date_range_label,
                event_label=week.event_label,
            )
            for week in weeks
        ]
    )
    _audit(membership, "work_plan.created", plan, term=term.name, weeks=len(weeks))
    return plan


@transaction.atomic
def save_work_plan_row(
    *, membership, plan, row, base_revision=None, objective_ids=None, remarks=None
):
    """Autosave one week row of a Work Plan."""
    assert_is_author(membership, plan)
    _guard_writable(plan)
    _check_revision(plan, base_revision)

    if row.work_plan_id != plan.pk:
        raise PermissionDenied("That row belongs to another plan.")

    if objective_ids is not None:
        if objective_ids:
            objectives = resolve_selected_objectives(
                membership, objective_ids, topic_id=_topic_for(objective_ids, membership)
            )
            row.learning_objectives.set(objectives)
            row.objective_labels = [item.label for item in objectives]
            first = objectives[0]
            row.topic_id = first.topic_id
            row.subtopic_id = first.subtopic_id
        else:
            row.learning_objectives.clear()
            row.objective_labels = []
            row.topic_id = None
            row.subtopic_id = None

    if remarks is not None:
        row.remarks = remarks

    row.save(update_fields=("topic", "subtopic", "objective_labels", "remarks", "updated_at"))
    return _bump(plan, [])


def _topic_for(objective_ids, membership):
    """Derive the topic from the first submitted objective, safely."""
    from apps.curriculum.models import LearningObjective

    first = (
        LearningObjective.objects.for_school(membership.school_id)
        .filter(pk=objective_ids[0])
        .first()
    )
    if first is None:
        raise PermissionDenied("One or more selected objectives are not authorised.")
    return first.topic_id


@transaction.atomic
def save_work_plan_resources(*, membership, plan, resources, base_revision=None):
    """WP-T02, the page-three resources area."""
    assert_is_author(membership, plan)
    _guard_writable(plan)
    _check_revision(plan, base_revision)
    plan.resources = resources
    return _bump(plan, ["resources"])


def _coerce_date(value):
    """Accept an ISO string or a date, since API and form callers differ."""
    if isinstance(value, str):
        parsed = parse_date(value)
        if parsed is None:
            raise ValidationError({"lesson_date": "Provide a valid date as YYYY-MM-DD."})
        return parsed
    if value is None:
        raise ValidationError({"lesson_date": "A lesson date is required."})
    return value


@transaction.atomic
def create_lesson_plan(*, membership, assignment_id, lesson_date, work_plan_row=None):
    """Open a Lesson Plan, optionally carrying forward a Work Plan row (7.3)."""
    lesson_date = _coerce_date(lesson_date)
    assignment = resolve_assignment(membership, assignment_id, on_date=lesson_date)
    version = _require_template(membership.school, PlanType.LESSON_PLAN)

    plan = LessonPlan.all_objects.create(
        school_id=membership.school_id,
        template_version=version,
        assignment=assignment,
        author_id=membership.user_id,
        lesson_date=lesson_date,
        work_plan_row=work_plan_row,
    )

    if work_plan_row is not None:
        if work_plan_row.school_id != membership.school_id:
            raise PermissionDenied("That work plan row belongs to another school.")
        if work_plan_row.work_plan.assignment_id != assignment.pk:
            raise PermissionDenied("That work plan row is for a different assignment.")
        # Carry forward assignment, unit and objectives (7.3, 13.2).
        plan.topic_id = work_plan_row.topic_id
        plan.subtopic_id = work_plan_row.subtopic_id
        plan.objective_labels = list(work_plan_row.objective_labels)
        plan.save(update_fields=("topic", "subtopic", "objective_labels", "updated_at"))
        plan.learning_objectives.set(work_plan_row.learning_objectives.all())

    _audit(
        membership,
        "lesson_plan.created",
        plan,
        lesson_date=str(lesson_date),
        from_work_plan=bool(work_plan_row),
    )
    return plan


@transaction.atomic
def save_lesson_plan(
    *,
    membership,
    plan,
    base_revision=None,
    subtopic_id=...,
    objective_ids=None,
    boys_present=...,
    girls_present=...,
    main_teaching_activity=None,
    assessment_ideas=None,
    notes_remarks=None,
    attendance_exception=None,
):
    """Autosave a Lesson Plan draft.

    Sentinel defaults distinguish "not supplied" from an explicit ``None``.
    """
    assert_is_author(membership, plan)
    _guard_writable(plan)
    _check_revision(plan, base_revision)

    update_fields = []

    if subtopic_id is not ...:
        from apps.curriculum.services import subtopic_options

        if subtopic_id:
            subtopic = None
            from apps.curriculum.models import Subtopic

            candidate = (
                Subtopic.objects.for_school(membership.school_id).filter(pk=subtopic_id).first()
            )
            if candidate is None:
                raise PermissionDenied("That sub-unit is not available.")
            # Re-check through the scoped service so assignment scope is applied.
            allowed = {str(item.pk) for item in subtopic_options(membership, candidate.topic_id)}
            if str(subtopic_id) not in allowed:
                raise PermissionDenied("That sub-unit is not available for your assignments.")
            subtopic = candidate
            plan.subtopic_id = subtopic.pk
            plan.topic_id = subtopic.topic_id
        else:
            plan.subtopic_id = None
        update_fields += ["subtopic", "topic"]

    if objective_ids is not None:
        objectives = resolve_selected_objectives(
            membership,
            objective_ids,
            topic_id=plan.topic_id,
            subtopic_id=plan.subtopic_id,
        )
        plan.learning_objectives.set(objectives)
        plan.objective_labels = [item.label for item in objectives]
        update_fields.append("objective_labels")

    if boys_present is not ... or girls_present is not ...:
        boys = plan.boys_present if boys_present is ... else boys_present
        girls = plan.girls_present if girls_present is ... else girls_present
        validate_attendance(
            plan,
            boys=boys,
            girls=girls,
            allow_exception=bool(attendance_exception or plan.attendance_exception),
        )
        plan.boys_present = boys
        plan.girls_present = girls
        update_fields += ["boys_present", "girls_present"]

    if attendance_exception is not None:
        plan.attendance_exception = attendance_exception
        update_fields.append("attendance_exception")

    for name, value in (
        ("main_teaching_activity", main_teaching_activity),
        ("assessment_ideas", assessment_ideas),
        ("notes_remarks", notes_remarks),
    ):
        if value is not None:
            setattr(plan, name, value)
            update_fields.append(name)

    return _bump(plan, update_fields)


def visible_plans(membership, model):
    """Plans the caller may list (6.2). Teachers see only their own."""
    queryset = model.objects.for_school(membership.school_id).select_related(
        "assignment__subject", "assignment__school_class", "author"
    )
    if membership.role == Membership.Role.TEACHER:
        return queryset.filter(author_id=membership.user_id)
    return queryset


def review_queue(membership, model):
    """Plans awaiting a reviewer decision, excluding the reviewer's own."""
    from apps.plans.models import PENDING_STATES

    return (
        visible_plans(membership, model)
        .filter(state__in=PENDING_STATES)
        .exclude(author_id=membership.user_id)
    )


def week_rows_for(plan):
    """Ordered Work Plan rows with their curriculum context preloaded."""
    return (
        WorkPlanRow.all_objects.filter(school_id=plan.school_id, work_plan=plan)
        .select_related("topic", "subtopic", "calendar_week")
        .order_by("week_number")
    )


def ensure_weeks(term):
    """Convenience wrapper so views can lazily build a term calendar."""
    if not CalendarWeek.all_objects.filter(term=term).exists():
        return generate_weeks(term)
    return list(CalendarWeek.all_objects.filter(term=term).order_by("number"))
