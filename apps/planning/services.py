"""Permission-aware creation, autosave and workflow services for Work Plans."""

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.curriculum.models import LearningObjective, Subtopic, Topic
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
from apps.schools.models import CalendarWeek, Membership, TeacherAssignment


def _ensure_assignment_is_active(assignment):
    today = timezone.localdate()
    if not assignment.is_active or assignment.effective_from > today:
        raise ValidationError("This teaching assignment is not active.")
    if assignment.effective_until and assignment.effective_until < today:
        raise ValidationError("This teaching assignment has expired.")


def create_work_plan(*, school, author, academic_year, term, scheme, subject=None, school_class=None, assignment=None):
    """Create a draft and snapshot the term's school-calendar weeks, or return existing shared work plan."""

    if assignment:
        if assignment.school_id != school.id:
            raise PermissionDenied("The assignment does not belong to this school.")
        subject = assignment.subject
        school_class = assignment.school_class

    if not subject or not school_class:
        raise ValidationError("Subject and school class are required to create a Work Plan.")

    # Single Source of Truth: Check if an active Work Plan already exists in this school for this subject, class & term
    existing = WorkPlan.all_objects.filter(
        school=school,
        term=term,
        subject=subject,
        school_class=school_class,
    ).exclude(status=WorkPlan.Status.ARCHIVED).first()

    if existing:
        return existing

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

    calendar_weeks = list(CalendarWeek.all_objects.filter(term=term).order_by("sequence"))
    if not calendar_weeks:
        raise ValidationError("Add school calendar weeks to this term before creating a Work Plan.")

    with transaction.atomic():
        plan = WorkPlan(
            school=school,
            subject=subject,
            school_class=school_class,
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
                lessons_per_week=1 if calendar_week.is_instructional else 0,
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

    if not plan.is_editable:
        raise PermissionDenied("Only draft or returned Work Plans can be edited.")
    if revision != plan.revision:
        raise ValidationError("This plan changed elsewhere. Refresh before saving.")

    supplied_ids = {str(item["id"]) for item in week_updates}
    actual_ids = {str(item.pk) for item in plan.weeks.all()}
    if supplied_ids != actual_ids:
        raise ValidationError("A save must include exactly this plan's calendar weeks.")

    with transaction.atomic():
        for item in week_updates:
            week = plan.weeks.get(pk=item["id"])
            if week.is_instructional:
                week.topic_id = item.get("topic_id") or None
                week.subtopic_id = item.get("subtopic_id") or None
                lessons = item.get("lessons_per_week")
                if lessons is not None:
                    week.lessons_per_week = int(lessons)
                elif week.lessons_per_week == 0:
                    week.lessons_per_week = 1
            else:
                week.topic_id = None
                week.subtopic_id = None
                week.lessons_per_week = 0

            week.remarks = item.get("remarks", "")
            week.full_clean()
            week.save(
                update_fields=("topic", "subtopic", "lessons_per_week", "remarks", "updated_at")
            )
            WorkPlanWeekObjective.all_objects.filter(work_plan_week=week).delete()
            if week.is_instructional:
                seen = set()
                deduped_objectives = []
                for obj_id in item.get("objectives", []):
                    obj_str = str(obj_id)
                    if obj_str not in seen and obj_str:
                        seen.add(obj_str)
                        deduped_objectives.append(obj_id)
                for objective in deduped_objectives:
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


def update_work_plan_weekly_reflections(
    *, plan, actor, week_remarks: dict[str, str], met_objective_ids: set[str]
):
    """Update Friday remarks and met objectives on an approved or editable Work Plan for elapsed/current weeks."""
    if plan.author_id != actor.id:
        raise PermissionDenied("Only the author can update weekly remarks on this Work Plan.")
    if plan.status != WorkPlan.Status.APPROVED and not plan.is_editable:
        raise ValidationError(
            "Weekly remarks can only be updated on approved or editable Work Plans."
        )

    today = timezone.localdate()

    with transaction.atomic():
        for week in plan.weeks.select_related("calendar_week").prefetch_related(
            "objective_selections"
        ):
            week_id_str = str(week.pk)
            # Check if week is eligible (starts_on <= today, meaning the week has begun/ended)
            is_past_or_current = (
                week.calendar_week and week.calendar_week.starts_on <= today
            ) or not week.calendar_week

            if not is_past_or_current and plan.status == WorkPlan.Status.APPROVED:
                # Cannot remark on future weeks
                continue

            # Update remarks if provided
            if week_id_str in week_remarks:
                new_remarks = week_remarks[week_id_str].strip()
                if new_remarks != week.remarks:
                    week.remarks = new_remarks
                    week.save(update_fields=["remarks", "updated_at"])

            # Update met status of planned objectives
            for sel in week.objective_selections.all():
                obj_id_str = str(sel.objective_id)
                sel_id_str = str(sel.pk)
                is_met = (obj_id_str in met_objective_ids) or (sel_id_str in met_objective_ids)
                if is_met != sel.is_met:
                    sel.is_met = is_met
                    sel.met_at = timezone.now() if is_met else None
                    sel.save(update_fields=["is_met", "met_at", "updated_at"])

    return plan


def get_work_plan_reflection_stats(plan):
    """Return past weeks reflection progress and real-time achieved/met objective coverage."""
    today = timezone.localdate()
    weeks = list(
        plan.weeks.select_related("calendar_week")
        .prefetch_related("objective_selections")
        .order_by("sequence")
    )

    total_weeks = len(weeks)
    past_weeks = []

    for w in weeks:
        is_past = bool(w.calendar_week and w.calendar_week.ends_on <= today)
        is_current = bool(
            w.calendar_week
            and w.calendar_week.starts_on <= today
            and w.calendar_week.ends_on > today
        )
        w.is_past_week = is_past
        w.is_current_week = is_current
        w.can_remark = is_past or is_current or (plan.status != WorkPlan.Status.APPROVED)
        if is_past or is_current:
            past_weeks.append(w)

    past_weeks_count = len(past_weeks)
    remarked_past_weeks_count = sum(1 for w in past_weeks if (w.remarks and w.remarks.strip()))
    remarks_progress_percent = (
        round((remarked_past_weeks_count / past_weeks_count) * 100, 1)
        if past_weeks_count > 0
        else 100.0
    )

    all_selections = [sel for w in weeks for sel in w.objective_selections.all()]
    total_planned_objectives = len(set(sel.objective_id for sel in all_selections))
    met_objectives_count = len(set(sel.objective_id for sel in all_selections if sel.is_met))
    met_coverage_percent = (
        round((met_objectives_count / total_planned_objectives) * 100, 1)
        if total_planned_objectives > 0
        else 0.0
    )

    return {
        "total_weeks": total_weeks,
        "past_weeks_count": past_weeks_count,
        "remarked_past_weeks_count": remarked_past_weeks_count,
        "remarks_progress_percent": remarks_progress_percent,
        "total_planned_objectives": total_planned_objectives,
        "met_objectives_count": met_objectives_count,
        "met_coverage_percent": met_coverage_percent,
    }


ELIGIBLE_PREVIOUS_COVERAGE_STATUSES = {WorkPlan.Status.APPROVED, WorkPlan.Status.ARCHIVED}


def get_previous_covered_objective_ids(
    *, school, school_class, subject, scheme, before_date, exclude_plan_id=None
):
    """Return distinct UUIDs of LearningObjectives covered in eligible previous Work Plans.

    Scoped to:
      - same school
      - same school_class (from assignment.school_class)
      - same subject (from assignment.subject)
      - same scheme
      - status in ELIGIBLE_PREVIOUS_COVERAGE_STATUSES
      - term.ends_on < before_date
    """
    qs = WorkPlanWeekObjective.all_objects.filter(
        school=school,
        work_plan_week__work_plan__assignment__school_class=school_class,
        work_plan_week__work_plan__assignment__subject=subject,
        work_plan_week__work_plan__scheme=scheme,
        work_plan_week__work_plan__status__in=ELIGIBLE_PREVIOUS_COVERAGE_STATUSES,
        work_plan_week__work_plan__term__ends_on__lt=before_date,
    )
    if exclude_plan_id:
        qs = qs.exclude(work_plan_week__work_plan_id=exclude_plan_id)
    return set(qs.values_list("objective_id", flat=True).distinct())


def calculate_work_plan_coverage(work_plan, selected_objective_ids=None):
    """Calculate historical, current, and projected curriculum and topic coverage for a Work Plan."""
    scheme = work_plan.scheme
    school = work_plan.school
    school_class = work_plan.school_class or (work_plan.assignment.school_class if work_plan.assignment else None)
    subject = work_plan.subject or (work_plan.assignment.subject if work_plan.assignment else None)
    before_date = work_plan.term.starts_on

    # All objectives in scheme
    all_scheme_objs = list(
        LearningObjective.objects.filter(scheme=scheme).values(
            "id", "topic_id", "subtopic_id", "code", "text"
        )
    )
    total_objectives = len(all_scheme_objs)

    # Previously covered objectives
    previous_obj_ids = get_previous_covered_objective_ids(
        school=school,
        school_class=school_class,
        subject=subject,
        scheme=scheme,
        before_date=before_date,
        exclude_plan_id=work_plan.id,
    )
    previously_covered_count = len(previous_obj_ids)

    # Current plan selected objectives
    if selected_objective_ids is None:
        current_obj_ids = set(
            WorkPlanWeekObjective.all_objects.filter(
                work_plan_week__work_plan=work_plan
            ).values_list("objective_id", flat=True)
        )
    else:
        current_obj_ids = set(selected_objective_ids)
    current_plan_objectives = len(current_obj_ids)

    # Projected coverage (union)
    projected_covered_ids = previous_obj_ids | current_obj_ids
    projected_covered_count = len(projected_covered_ids)
    remaining_objectives = max(0, total_objectives - projected_covered_count)

    # Percentage calculations (safe against 0 total)
    if total_objectives > 0:
        previous_objective_percent = round((previously_covered_count / total_objectives) * 100, 1)
        projected_objective_percent = round((projected_covered_count / total_objectives) * 100, 1)
    else:
        previous_objective_percent = 0.0
        projected_objective_percent = 0.0

    # Topic coverage calculation
    topics_with_objs = {}
    for obj in all_scheme_objs:
        t_id = obj["topic_id"]
        if t_id:
            if t_id not in topics_with_objs:
                topics_with_objs[t_id] = set()
            topics_with_objs[t_id].add(obj["id"])

    total_topics = len(topics_with_objs)
    covered_topics = 0
    for _t_id, obj_set in topics_with_objs.items():
        if obj_set & projected_covered_ids:
            covered_topics += 1

    if total_topics > 0:
        projected_topic_percent = round((covered_topics / total_topics) * 100, 1)
    else:
        projected_topic_percent = 0.0

    return {
        "total_objectives": total_objectives,
        "previously_covered_objectives": previously_covered_count,
        "current_plan_objectives": current_plan_objectives,
        "projected_covered_objectives": projected_covered_count,
        "remaining_objectives": remaining_objectives,
        "previous_objective_percent": previous_objective_percent,
        "projected_objective_percent": projected_objective_percent,
        "total_topics": total_topics,
        "covered_topics": covered_topics,
        "projected_topic_percent": projected_topic_percent,
    }


def get_curriculum_coverage_data(work_plan):
    """Return enriched curriculum tree with topic/unit coverage stats and objective availability."""
    scheme = work_plan.scheme
    school = work_plan.school
    school_class = work_plan.school_class or (work_plan.assignment.school_class if work_plan.assignment else None)
    subject = work_plan.subject or (work_plan.assignment.subject if work_plan.assignment else None)
    before_date = work_plan.term.starts_on

    previous_obj_ids = get_previous_covered_objective_ids(
        school=school,
        school_class=school_class,
        subject=subject,
        scheme=scheme,
        before_date=before_date,
        exclude_plan_id=work_plan.id,
    )
    current_obj_ids = set(
        WorkPlanWeekObjective.all_objects.filter(work_plan_week__work_plan=work_plan).values_list(
            "objective_id", flat=True
        )
    )

    topics = list(Topic.objects.filter(scheme=scheme).order_by("sequence"))
    subtopics = list(
        Subtopic.objects.filter(topic__scheme=scheme).select_related("topic").order_by("sequence")
    )
    objectives = list(
        LearningObjective.objects.filter(scheme=scheme)
        .select_related("topic", "subtopic")
        .order_by("sequence")
    )

    topic_objs = {}
    subtopic_objs = {}
    for obj in objectives:
        t_id = str(obj.topic_id) if obj.topic_id else None
        st_id = str(obj.subtopic_id) if obj.subtopic_id else None
        if t_id:
            topic_objs.setdefault(t_id, []).append(obj)
        if st_id:
            subtopic_objs.setdefault(st_id, []).append(obj)

    topic_list = []
    for t in topics:
        t_id_str = str(t.pk)
        objs = topic_objs.get(t_id_str, [])
        total_count = len(objs)
        prev_covered_count = sum(1 for o in objs if o.pk in previous_obj_ids)
        available_count = total_count - prev_covered_count
        covered_percent = (
            round((prev_covered_count / total_count) * 100, 1) if total_count > 0 else 0.0
        )
        topic_list.append(
            {
                "id": t_id_str,
                "title": t.title,
                "sequence": t.sequence,
                "total_objectives_count": total_count,
                "previously_covered_count": prev_covered_count,
                "available_objectives_count": available_count,
                "covered_percent": covered_percent,
                "is_fully_covered": total_count > 0 and available_count == 0,
            }
        )

    unit_list = []
    for st in subtopics:
        st_id_str = str(st.pk)
        objs = subtopic_objs.get(st_id_str, [])
        total_count = len(objs)
        prev_covered_count = sum(1 for o in objs if o.pk in previous_obj_ids)
        available_count = total_count - prev_covered_count
        covered_percent = (
            round((prev_covered_count / total_count) * 100, 1) if total_count > 0 else 0.0
        )
        unit_list.append(
            {
                "id": st_id_str,
                "topic_id": str(st.topic_id),
                "title": st.title,
                "sequence": st.sequence,
                "total_objectives_count": total_count,
                "previously_covered_count": prev_covered_count,
                "available_objectives_count": available_count,
                "covered_percent": covered_percent,
                "is_fully_covered": total_count > 0 and available_count == 0,
            }
        )

    objective_list = []
    for obj in objectives:
        is_prev = obj.pk in previous_obj_ids
        is_curr = obj.pk in current_obj_ids
        objective_list.append(
            {
                "id": str(obj.pk),
                "code": obj.code,
                "text": obj.text,
                "topic_id": str(obj.topic_id) if obj.topic_id else None,
                "subtopic_id": str(obj.subtopic_id) if obj.subtopic_id else None,
                "topic_title": obj.topic.title if obj.topic_id else "",
                "subtopic_title": obj.subtopic.title if obj.subtopic_id else "",
                "is_available": not is_prev,
                "previously_covered": is_prev,
                "selected_in_current_plan": is_curr,
            }
        )

    coverage = calculate_work_plan_coverage(work_plan, selected_objective_ids=current_obj_ids)

    from apps.curriculum.cross_curriculum import (
        get_cross_borrowable_schemes,
        get_smart_cross_subject_hints,
    )

    companion_schemes_qs = get_cross_borrowable_schemes(scheme)
    companion_schemes_data = []

    for cs in companion_schemes_qs:
        cs_topics = list(Topic.objects.filter(scheme=cs).order_by("sequence"))
        cs_subtopics = list(
            Subtopic.objects.filter(topic__scheme=cs).select_related("topic").order_by("sequence")
        )
        cs_objectives = list(
            LearningObjective.objects.filter(scheme=cs)
            .select_related("topic", "subtopic")
            .order_by("sequence")
        )

        cs_topic_objs = {}
        cs_subtopic_objs = {}
        for obj in cs_objectives:
            t_id = str(obj.topic_id) if obj.topic_id else None
            st_id = str(obj.subtopic_id) if obj.subtopic_id else None
            if t_id:
                cs_topic_objs.setdefault(t_id, []).append(obj)
            if st_id:
                cs_subtopic_objs.setdefault(st_id, []).append(obj)

        cs_topic_list = []
        for t in cs_topics:
            t_id_str = str(t.pk)
            objs = cs_topic_objs.get(t_id_str, [])
            total_count = len(objs)
            cs_topic_list.append(
                {
                    "id": t_id_str,
                    "title": t.title,
                    "sequence": t.sequence,
                    "total_objectives_count": total_count,
                    "available_objectives_count": total_count,
                    "covered_percent": 0.0,
                    "is_fully_covered": False,
                    "scheme_id": str(cs.id),
                    "scheme_subject": cs.subject_name,
                }
            )

        cs_unit_list = []
        for st in cs_subtopics:
            st_id_str = str(st.pk)
            objs = cs_subtopic_objs.get(st_id_str, [])
            total_count = len(objs)
            cs_unit_list.append(
                {
                    "id": st_id_str,
                    "topic_id": str(st.topic_id),
                    "title": st.title,
                    "sequence": st.sequence,
                    "total_objectives_count": total_count,
                    "available_objectives_count": total_count,
                    "covered_percent": 0.0,
                    "is_fully_covered": False,
                    "scheme_id": str(cs.id),
                    "scheme_subject": cs.subject_name,
                }
            )

        cs_objective_list = []
        for obj in cs_objectives:
            is_curr = obj.pk in current_obj_ids
            cs_objective_list.append(
                {
                    "id": str(obj.pk),
                    "code": obj.code,
                    "text": obj.text,
                    "topic_id": str(obj.topic_id) if obj.topic_id else None,
                    "subtopic_id": str(obj.subtopic_id) if obj.subtopic_id else None,
                    "topic_title": obj.topic.title if obj.topic_id else "",
                    "subtopic_title": obj.subtopic.title if obj.subtopic_id else "",
                    "is_available": True,
                    "previously_covered": False,
                    "selected_in_current_plan": is_curr,
                    "is_cross_curricular": True,
                    "scheme_id": str(cs.id),
                    "scheme_subject": cs.subject_name,
                    "scheme_title": cs.title,
                }
            )

        companion_schemes_data.append(
            {
                "id": str(cs.id),
                "title": cs.title,
                "subject_name": cs.subject_name,
                "year_group": cs.year_group,
                "topics": cs_topic_list,
                "units": cs_unit_list,
                "objectives": cs_objective_list,
            }
        )

    smart_hints = get_smart_cross_subject_hints(scheme, companion_schemes_qs)

    return {
        "scheme_id": str(scheme.id),
        "scheme_title": scheme.title,
        "scheme_subject": scheme.subject_name,
        "topics": topic_list,
        "units": unit_list,
        "objectives": objective_list,
        "companion_schemes": companion_schemes_data,
        "smart_hints": smart_hints,
        "coverage": coverage,
    }


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


def get_available_lesson_plan_objectives(lesson_plan):
    """Retrieve planned objectives scoped to the Lesson Plan's Work Plan week, with sequential borrowing support."""
    origin_week = lesson_plan.originating_work_plan_week
    if not origin_week:
        all_objs = (
            LearningObjective.objects.filter(scheme=lesson_plan.scheme)
            .select_related("topic", "subtopic")
            .order_by("sequence")
        )
        selected_ids = set(
            lesson_plan.objective_selections.values_list("objective_id", flat=True)
        )
        return {
            "origin_week": None,
            "current_week_objectives": [
                {
                    "id": str(obj.pk),
                    "objective": obj,
                    "code": obj.code,
                    "text": obj.text,
                    "topic_title": obj.topic.title if obj.topic else "",
                    "subtopic_title": obj.subtopic.title if obj.subtopic else "",
                    "is_used_in_other_lessons": False,
                    "is_selected_in_this_lesson": obj.pk in selected_ids,
                    "is_borrowed": False,
                }
                for obj in all_objs
            ],
            "is_current_week_exhausted": False,
            "remaining_current_week_count": len(all_objs),
            "borrowable_weeks": [],
        }

    work_plan = origin_week.work_plan
    # Find all objectives selected in other lesson plans in this work plan
    assigned_in_other_plans = set(
        LessonPlanObjective.all_objects.filter(
            lesson_plan__originating_work_plan_week__work_plan=work_plan
        )
        .exclude(lesson_plan_id=lesson_plan.pk)
        .values_list("objective_id", flat=True)
    )

    selected_in_this_lesson = set(
        lesson_plan.objective_selections.values_list("objective_id", flat=True)
    )

    # Current week's planned objectives
    current_week_selections = origin_week.objective_selections.select_related(
        "objective__topic", "objective__subtopic"
    ).all()
    current_week_objs = []
    unassigned_count = 0

    for sel in current_week_selections:
        obj = sel.objective
        is_used = obj.pk in assigned_in_other_plans
        is_selected = obj.pk in selected_in_this_lesson
        if not is_used or is_selected:
            unassigned_count += 1
        current_week_objs.append(
            {
                "id": str(obj.pk),
                "objective": obj,
                "code": sel.code_snapshot or obj.code,
                "text": sel.text_snapshot or obj.text,
                "topic_title": obj.topic.title if obj.topic else "",
                "subtopic_title": obj.subtopic.title if obj.subtopic else "",
                "is_used_in_other_lessons": is_used and not is_selected,
                "is_selected_in_this_lesson": is_selected,
                "is_borrowed": False,
            }
        )

    is_current_week_exhausted = (unassigned_count == 0) or all(
        item["is_used_in_other_lessons"] or item["is_selected_in_this_lesson"]
        for item in current_week_objs
    )

    # Sequential borrowing: find subsequent weeks with unassigned objectives
    borrowable_weeks = []
    subsequent_weeks = (
        work_plan.weeks.filter(
            sequence__gt=origin_week.sequence,
            is_instructional=True,
        )
        .order_by("sequence")
        .prefetch_related(
            "objective_selections__objective__topic",
            "objective_selections__objective__subtopic",
        )
    )

    for sw in subsequent_weeks:
        sw_objs = []
        for sel in sw.objective_selections.all():
            obj = sel.objective
            is_used = obj.pk in assigned_in_other_plans
            is_selected = obj.pk in selected_in_this_lesson
            if not is_used or is_selected:
                sw_objs.append(
                    {
                        "id": str(obj.pk),
                        "objective": obj,
                        "code": sel.code_snapshot or obj.code,
                        "text": sel.text_snapshot or obj.text,
                        "topic_title": obj.topic.title if obj.topic else "",
                        "subtopic_title": obj.subtopic.title if obj.subtopic else "",
                        "is_used_in_other_lessons": is_used and not is_selected,
                        "is_selected_in_this_lesson": is_selected,
                        "is_borrowed": True,
                        "source_week_sequence": sw.sequence,
                        "source_week_label": sw.week_label,
                    }
                )
        if sw_objs:
            borrowable_weeks.append(
                {
                    "week": sw,
                    "sequence": sw.sequence,
                    "week_label": sw.week_label,
                    "month_label": sw.month_label,
                    "topic_title": sw.topic.title if sw.topic else "",
                    "objectives": sw_objs,
                }
            )

    return {
        "origin_week": origin_week,
        "current_week_objectives": current_week_objs,
        "is_current_week_exhausted": is_current_week_exhausted,
        "remaining_current_week_count": unassigned_count,
        "borrowable_weeks": borrowable_weeks,
    }


def create_lesson_plan(
    *,
    school,
    author,
    assignment,
    academic_year,
    term,
    scheme,
    lesson_date,
    start_time=None,
    end_time=None,
    schedule_slot=None,
    topic=None,
    subtopic=None,
    origin=None,
    originating_work_plan_week=None,
):
    if originating_work_plan_week and not origin:
        origin = originating_work_plan_week
    if assignment.school_id != school.id or assignment.teacher_id != author.id:
        raise PermissionDenied("You can create Lesson Plans only for your active assignments.")
    _ensure_assignment_is_active(assignment)

    if isinstance(lesson_date, str):
        from django.utils.dateparse import parse_date
        parsed_d = parse_date(lesson_date)
        if parsed_d:
            lesson_date = parsed_d

    # Prospective check: cannot create for elapsed lessons
    now = timezone.localtime()
    from datetime import time, datetime
    et = end_time or time(23, 59, 59)
    lesson_end_dt = timezone.make_aware(datetime.combine(lesson_date, et), timezone.get_current_timezone())
    if lesson_end_dt < now:
        raise ValidationError("Lesson plans cannot be created for past lessons or elapsed lesson times.")

    if origin:
        if origin.calendar_week:
            cw = origin.calendar_week
            if not (cw.starts_on <= lesson_date <= cw.ends_on):
                raise ValidationError(
                    {
                        "lesson_date": f"The lesson date must fall within Week {origin.sequence} dates ({cw.starts_on:%d %b} – {cw.ends_on:%d %b %Y})."
                    }
                )
        if not topic:
            topic = origin.topic or scheme.topics.first()
        if not subtopic:
            subtopic = origin.subtopic

    if not topic:
        topic = Topic.objects.filter(scheme=scheme).first()
        if not topic:
            raise ValidationError("The selected scheme does not have any topics.")

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
            start_time=start_time,
            end_time=end_time,
            schedule_slot=schedule_slot,
            topic=topic,
            subtopic=subtopic,
            boys_attendance=None,
            girls_attendance=None,
            main_teaching_activity="",
            assessment_ideas="",
            resources=[],
            notes_remarks="",
        )
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

    # If lesson is not in the past yet, attendance and remarks cannot be saved
    if not plan.is_lesson_past:
        if "boys_attendance" in values:
            values["boys_attendance"] = None
        if "girls_attendance" in values:
            values["girls_attendance"] = None
        if "notes_remarks" in values and not plan.notes_remarks:
            values["notes_remarks"] = ""

    origin = plan.originating_work_plan_week
    if origin and origin.calendar_week and "lesson_date" in values:
        ld = values["lesson_date"]
        if isinstance(ld, str):
            from django.utils.dateparse import parse_date
            ld = parse_date(ld)
            if ld:
                values["lesson_date"] = ld
        if ld and not (origin.calendar_week.starts_on <= ld <= origin.calendar_week.ends_on):
            raise ValidationError(
                {
                    "lesson_date": f"The lesson date must fall within Week {origin.sequence} dates ({origin.calendar_week.starts_on:%d %b} – {origin.calendar_week.ends_on:%d %b %Y})."
                }
            )
    elif "lesson_date" in values and isinstance(values["lesson_date"], str):
        from django.utils.dateparse import parse_date
        parsed_d = parse_date(values["lesson_date"])
        if parsed_d:
            values["lesson_date"] = parsed_d

    with transaction.atomic():
        for field, value in values.items():
            setattr(plan, field, value)
        plan.clean()
        plan.revision += 1
        plan.revision_token = uuid.uuid4()
        plan.save()
        LessonPlanObjective.all_objects.filter(lesson_plan=plan).delete()
        for objective_id in objective_ids:
            try:
                obj = LearningObjective.objects.get(pk=objective_id)
                LessonPlanObjective.all_objects.create(
                    school=plan.school,
                    lesson_plan=plan,
                    objective=obj,
                    code_snapshot=obj.code,
                    text_snapshot=obj.text,
                )
            except LearningObjective.DoesNotExist:
                pass
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
    author_transition = target_status in {
        LessonPlan.Status.SUBMITTED,
        LessonPlan.Status.RESUBMITTED,
    }
    leadership = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if author_transition and plan.author_id != actor_membership.user_id:
        raise PermissionDenied("Only the author can submit or resubmit the Lesson Plan.")
    if not author_transition and actor_membership.role not in leadership:
        raise PermissionDenied("Only curriculum leadership can review or approve Lesson Plans.")
    if target_status == LessonPlan.Status.RETURNED and not comment.strip():
        raise ValidationError("A return requires a comment for the author.")
    if author_transition:
        if not plan.is_lesson_past:
            end_str = f" at {plan.end_time:%H:%M}" if plan.end_time else ""
            raise ValidationError(
                f"A Lesson Plan can only be submitted after the scheduled lesson concludes ({plan.lesson_date:%d %b %Y}{end_str})."
            )
        if plan.boys_attendance is None or plan.girls_attendance is None:
            raise ValidationError("Please record student attendance (boys & girls count) before submitting.")
        if not plan.notes_remarks or not plan.notes_remarks.strip():
            raise ValidationError("Please record teacher evaluation remarks before submitting.")

        has_objectives = LessonPlanObjective.all_objects.filter(lesson_plan=plan).exists()
        required = {
            "main_teaching_activity": plan.main_teaching_activity.strip(),
            "assessment_ideas": plan.assessment_ideas.strip(),
        }
        missing = [field.replace("_", " ") for field, value in required.items() if not value]
        if missing or not has_objectives:
            detail = ", ".join(
                missing + ([] if has_objectives else ["learning objectives"])
            )
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
