"""Permission-aware creation, autosave and workflow services for Work Plans."""

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.planning.models import (
    LessonPlan,
    LessonPlanEvent,
    LessonPlanObjective,
    PlanningTemplate,
    TemplateVersion,
    WorkPlan,
    WorkPlanEvent,
    WorkPlanWeek,
    WorkPlanWeekObjective,
)
from apps.schools.models import Membership, TeacherAssignment


def _ensure_assignment_is_active(assignment):
    today = timezone.localdate()
    if not assignment.is_active or assignment.effective_from > today:
        raise ValidationError("This teaching assignment is not active.")
    if assignment.effective_until and assignment.effective_until < today:
        raise ValidationError("This teaching assignment has expired.")


def create_work_plan(*, school, author, assignment, academic_year, term, scheme):
    """Create a draft and snapshot the term's school-calendar weeks."""

    if assignment.school_id != school.id or assignment.teacher_id != author.id:
        raise PermissionDenied("You can create Work Plans only for your active assignments.")
    _ensure_assignment_is_active(assignment)
    template_version = (
        TemplateVersion.all_objects.select_related("template")
        .filter(
            school=school,
            template__template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
            template__is_active=True,
            status=TemplateVersion.Status.PUBLISHED,
        )
        .order_by("-version")
        .first()
    )
    if template_version is None:
        raise ValidationError("No published Semester Work Plan template is available.")

    calendar_weeks = list(term.calendar_weeks.order_by("sequence"))
    if not calendar_weeks:
        raise ValidationError("Add school calendar weeks to this term before creating a Work Plan.")

    with transaction.atomic():
        plan = WorkPlan(
            school=school,
            assignment=assignment,
            academic_year=academic_year,
            term=term,
            scheme=scheme,
            template_version=template_version,
            author=author,
        )
        plan.full_clean()
        plan.save()
        for calendar_week in calendar_weeks:
            week = WorkPlanWeek(
                school=school,
                work_plan=plan,
                calendar_week=calendar_week,
                sequence=calendar_week.sequence,
                month_label=calendar_week.month_label,
                week_label=calendar_week.label,
                event_label=calendar_week.event_label,
                is_instructional=calendar_week.is_instructional,
            )
            week.full_clean()
            week.save()
        WorkPlanEvent.objects.create(
            school=school,
            work_plan=plan,
            actor=author,
            to_status=WorkPlan.Status.DRAFT,
            comment="Work Plan created.",
        )
    return plan


def save_work_plan(*, plan, actor, revision, resources, week_updates):
    """Apply an optimistic-locking draft save and replace each row's LO selections."""

    if plan.author_id != actor.id or not plan.is_editable:
        raise PermissionDenied("Only the author can edit a draft or returned Work Plan.")
    if revision != plan.revision:
        raise ValidationError("This plan changed elsewhere. Refresh before saving.")

    supplied_ids = {str(item["id"]) for item in week_updates}
    actual_ids = {str(item.pk) for item in plan.weeks.all()}
    if supplied_ids != actual_ids:
        raise ValidationError("A save must include exactly this plan's calendar weeks.")

    with transaction.atomic():
        for item in week_updates:
            week = plan.weeks.get(pk=item["id"])
            week.topic_id = item.get("topic_id") or None
            week.remarks = item.get("remarks", "")
            week.full_clean()
            week.save(update_fields=("topic", "remarks", "updated_at"))
            WorkPlanWeekObjective.all_objects.filter(work_plan_week=week).delete()
            for objective in item.get("objectives", []):
                WorkPlanWeekObjective.objects.create(
                    school=plan.school,
                    work_plan_week=week,
                    objective_id=objective,
                    code_snapshot="",
                    text_snapshot="",
                )
        plan.resources = resources
        plan.revision += 1
        plan.revision_token = uuid.uuid4()
        plan.save(update_fields=("resources", "revision", "revision_token", "updated_at"))
    return plan


_TRANSITIONS = {
    WorkPlan.Status.DRAFT: {WorkPlan.Status.SUBMITTED},
    WorkPlan.Status.RETURNED: {WorkPlan.Status.RESUBMITTED},
    WorkPlan.Status.RESUBMITTED: {WorkPlan.Status.UNDER_REVIEW},
    WorkPlan.Status.SUBMITTED: {
        WorkPlan.Status.UNDER_REVIEW,
        WorkPlan.Status.RETURNED,
        WorkPlan.Status.APPROVED,
    },
    WorkPlan.Status.UNDER_REVIEW: {WorkPlan.Status.RETURNED, WorkPlan.Status.APPROVED},
    WorkPlan.Status.APPROVED: {WorkPlan.Status.ARCHIVED},
}


def transition_work_plan(*, plan, actor_membership, target_status, comment=""):
    """Move a plan through its guarded, auditable workflow."""

    source_status = plan.status
    if target_status not in _TRANSITIONS.get(source_status, set()):
        raise ValidationError(f"Cannot move a {plan.get_status_display()} plan to {target_status}.")

    author_transition = target_status in {WorkPlan.Status.SUBMITTED, WorkPlan.Status.RESUBMITTED}
    reviewer_roles = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if author_transition:
        if plan.author_id != actor_membership.user_id:
            raise PermissionDenied("Only the plan author can submit or resubmit it.")
    elif actor_membership.role not in reviewer_roles:
        raise PermissionDenied("Only curriculum leadership can review or approve Work Plans.")
    if target_status == WorkPlan.Status.RETURNED and not comment.strip():
        raise ValidationError("A return requires a comment for the author.")

    with transaction.atomic():
        plan.status = target_status
        update_fields = ["status", "updated_at"]
        now = timezone.now()
        if target_status in {WorkPlan.Status.SUBMITTED, WorkPlan.Status.RESUBMITTED}:
            plan.submitted_at = now
            update_fields.append("submitted_at")
        elif target_status == WorkPlan.Status.APPROVED:
            plan.approved_at = now
            update_fields.append("approved_at")
        elif target_status == WorkPlan.Status.ARCHIVED:
            plan.archived_at = now
            update_fields.append("archived_at")
        plan.save(update_fields=update_fields)
        WorkPlanEvent.objects.create(
            school=plan.school,
            work_plan=plan,
            actor=actor_membership.user,
            from_status=source_status,
            to_status=target_status,
            comment=comment,
        )
    return plan


def active_assignment_for_user(*, school, user, assignment_id):
    assignment = TeacherAssignment.all_objects.select_related("subject", "school_class").get(
        pk=assignment_id, school=school, teacher=user
    )
    _ensure_assignment_is_active(assignment)
    return assignment


def create_lesson_plan(
    *, school, author, assignment, academic_year, term, scheme, lesson_date, topic, subtopic=None, origin=None
):
    if assignment.school_id != school.id or assignment.teacher_id != author.id:
        raise PermissionDenied("You can create Lesson Plans only for your active assignments.")
    _ensure_assignment_is_active(assignment)
    template_version = (
        TemplateVersion.all_objects.select_related("template")
        .filter(
            school=school,
            template__template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
            template__is_active=True,
            status=TemplateVersion.Status.PUBLISHED,
        )
        .order_by("-version")
        .first()
    )
    if template_version is None:
        raise ValidationError("No published Lesson Plan template is available.")
    with transaction.atomic():
        plan = LessonPlan(
            school=school,
            assignment=assignment,
            academic_year=academic_year,
            term=term,
            scheme=scheme,
            template_version=template_version,
            originating_work_plan_week=origin,
            author=author,
            lesson_date=lesson_date,
            topic=topic,
            subtopic=subtopic,
            boys_attendance=0,
            girls_attendance=0,
            main_teaching_activity="",
            assessment_ideas="",
        )
        # Incomplete controlled values are valid in a draft, but relationships are not.
        plan.clean()
        plan.save()
        LessonPlanEvent.objects.create(
            school=school,
            lesson_plan=plan,
            actor=author,
            to_status=LessonPlan.Status.DRAFT,
            comment="Lesson Plan created.",
        )
    return plan


def save_lesson_plan(*, plan, actor, revision, values, objective_ids):
    if plan.author_id != actor.id or not plan.is_editable:
        raise PermissionDenied("Only the author can edit a draft or returned Lesson Plan.")
    if revision != plan.revision:
        raise ValidationError("This plan changed elsewhere. Refresh before saving.")
    with transaction.atomic():
        for field, value in values.items():
            setattr(plan, field, value)
        plan.clean()
        plan.revision += 1
        plan.revision_token = uuid.uuid4()
        plan.save()
        LessonPlanObjective.all_objects.filter(lesson_plan=plan).delete()
        for objective_id in objective_ids:
            LessonPlanObjective.objects.create(
                school=plan.school,
                lesson_plan=plan,
                objective_id=objective_id,
                code_snapshot="",
                text_snapshot="",
            )
    return plan


def transition_lesson_plan(*, plan, actor_membership, target_status, comment=""):
    transitions = {
        LessonPlan.Status.DRAFT: {LessonPlan.Status.SUBMITTED},
        LessonPlan.Status.RETURNED: {LessonPlan.Status.RESUBMITTED},
        LessonPlan.Status.RESUBMITTED: {LessonPlan.Status.UNDER_REVIEW},
        LessonPlan.Status.SUBMITTED: {
            LessonPlan.Status.UNDER_REVIEW,
            LessonPlan.Status.RETURNED,
            LessonPlan.Status.APPROVED,
        },
        LessonPlan.Status.UNDER_REVIEW: {LessonPlan.Status.RETURNED, LessonPlan.Status.APPROVED},
    }
    if target_status not in transitions.get(plan.status, set()):
        raise ValidationError("This Lesson Plan cannot make that workflow transition.")
    author_transition = target_status in {LessonPlan.Status.SUBMITTED, LessonPlan.Status.RESUBMITTED}
    leadership = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if author_transition and plan.author_id != actor_membership.user_id:
        raise PermissionDenied("Only the author can submit or resubmit the Lesson Plan.")
    if not author_transition and actor_membership.role not in leadership:
        raise PermissionDenied("Only curriculum leadership can review or approve Lesson Plans.")
    if target_status == LessonPlan.Status.RETURNED and not comment.strip():
        raise ValidationError("A return requires a comment for the author.")
    if author_transition:
        required = {
            "main_teaching_activity": plan.main_teaching_activity.strip(),
            "assessment_ideas": plan.assessment_ideas.strip(),
        }
        missing = [field.replace("_", " ") for field, value in required.items() if not value]
        if missing or not plan.objective_selections.exists():
            detail = ", ".join(missing + ([] if plan.objective_selections.exists() else ["learning objectives"]))
            raise ValidationError(f"Complete {detail} before submitting.")
    source = plan.status
    with transaction.atomic():
        plan.status = target_status
        fields = ["status", "updated_at"]
        if target_status in {LessonPlan.Status.SUBMITTED, LessonPlan.Status.RESUBMITTED}:
            plan.submitted_at = timezone.now()
            fields.append("submitted_at")
        if target_status == LessonPlan.Status.APPROVED:
            plan.approved_at = timezone.now()
            fields.append("approved_at")
        plan.save(update_fields=fields)
        LessonPlanEvent.objects.create(
            school=plan.school,
            lesson_plan=plan,
            actor=actor_membership.user,
            from_status=source,
            to_status=target_status,
            comment=comment,
        )
    return plan
