from datetime import datetime, time, timedelta
from io import BytesIO

from django.db import models
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.curriculum.models import CurriculumFramework, LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.planning.forms import LessonPlanCreateForm, WorkPlanCreateForm
from apps.planning.models import LessonPlan, WorkPlan
from apps.planning.pdf import render_lesson_plan, render_work_plan
from apps.planning.services import (
    calculate_work_plan_coverage,
    create_lesson_plan,
    create_work_plan,
    get_available_lesson_plan_objectives,
    get_curriculum_coverage_data,
    get_work_plan_reflection_stats,
    save_lesson_plan,
    save_work_plan,
    transition_lesson_plan,
    transition_work_plan,
    update_work_plan_weekly_reflections,
)
from apps.schools.models import (
    Membership,
    SchoolClass,
    Subject,
    TeacherAssignment,
    TeacherScheduleSlot,
    TeacherTimetable,
    Term,
)


import re
from django.urls import reverse

def classify_class_tier(school_class):
    """Determine the Cambridge Framework tier for a SchoolClass based on its year_group and name."""
    yg = (school_class.year_group or "").lower()
    name = (school_class.name or "").lower()
    combined = f"{yg} {name}"

    if any(k in combined for k in ["stage 12", "stage 13", "year 12", "year 13", "grade 12", "grade 13", "as-yr", "as level", "a-level", "a level", "a2"]):
        return "CAMBRIDGE_AS_A_LEVEL"
    if any(k in combined for k in ["stage 10", "stage 11", "year 10", "year 11", "grade 10", "grade 11", "igcse"]):
        return "CAMBRIDGE_IGCSE"
    if any(k in combined for k in ["stage 7", "stage 8", "stage 9", "year 7", "year 8", "year 9", "grade 7", "grade 8", "grade 9", "9-pacific", "9-atlantic", "8b", "7a"]):
        return "CAMBRIDGE_LOWER_SECONDARY"
    if any(k in combined for k in ["stage 1", "stage 2", "stage 3", "stage 4", "stage 5", "stage 6", "year 1", "year 2", "year 3", "year 4", "year 5", "year 6", "grade 1", "grade 2", "grade 3", "grade 4", "grade 5", "grade 6", "reception", "eyfs", "kg"]):
        return "CAMBRIDGE_PRIMARY"

    digits = re.findall(r"\d+", combined)
    if digits:
        val = int(digits[0])
        if 1 <= val <= 6:
            return "CAMBRIDGE_PRIMARY"
        elif 7 <= val <= 9:
            return "CAMBRIDGE_LOWER_SECONDARY"
        elif 10 <= val <= 11:
            return "CAMBRIDGE_IGCSE"
        elif 12 <= val <= 13:
            return "CAMBRIDGE_AS_A_LEVEL"

    return "CAMBRIDGE_PRIMARY"


@login_required
@school_required
def work_plan_list(request):
    form = WorkPlanCreateForm(request.POST or None, school=request.school, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            plan = create_work_plan(
                school=request.school,
                author=request.user,
                subject=form.cleaned_data.get("subject"),
                school_class=form.cleaned_data.get("school_class"),
                assignment=form.cleaned_data.get("assignment"),
                academic_year=form.cleaned_data["academic_year"],
                term=form.cleaned_data["term"],
                scheme=form.cleaned_data["scheme"],
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            if plan.author_id == request.user.id and plan.events.count() <= 1:
                messages.success(request, "Work Plan created from the current school calendar.")
            else:
                messages.info(request, f"Loaded active Work Plan for {plan.subject_display} · {plan.class_display} ({plan.term.name}).")
            return redirect("planning:work_plan_detail", plan_id=plan.pk)

    # All active Work Plans in this school (Single Source of Truth)
    plans = (
        WorkPlan.objects.filter(school=request.school)
        .exclude(status=WorkPlan.Status.ARCHIVED)
        .select_related(
            "subject", "school_class", "assignment__subject", "assignment__school_class", "term", "scheme", "author"
        )
        .order_by("-updated_at")
    )

    all_classes = SchoolClass.objects.filter(school=request.school).order_by("name")
    all_subjects = Subject.objects.filter(school=request.school).order_by("name")

    today = timezone.localdate()
    teacher_assignments = (
        TeacherAssignment.objects.filter(
            school=request.school,
            teacher=request.user,
            is_active=True,
            effective_from__lte=today,
        )
        .filter(models.Q(effective_until__isnull=True) | models.Q(effective_until__gte=today))
        .select_related("subject", "school_class")
    )

    classes_meta = [
        {
            "id": str(c.pk),
            "name": c.name,
            "year_group": c.year_group or "",
            "framework_code": classify_class_tier(c),
        }
        for c in all_classes
    ]

    subjects_meta = [
        {
            "id": str(s.pk),
            "name": s.name,
            "code": s.code,
            "cambridge_code": s.cambridge_code or "",
        }
        for s in all_subjects
    ]

    assignment_meta = [
        {
            "id": str(a.pk),
            "subject_id": str(a.subject_id),
            "class_id": str(a.school_class_id),
            "subject_name": a.subject.name,
            "subject_code": a.subject.cambridge_code or a.subject.code,
            "class_name": a.school_class.name,
            "year_group": a.school_class.year_group or "",
            "framework_code": classify_class_tier(a.school_class),
        }
        for a in teacher_assignments
    ]

    existing_plans_meta = [
        {
            "id": str(p.pk),
            "subject_id": str(p.subject_id or (p.assignment.subject_id if p.assignment else "")),
            "class_id": str(p.school_class_id or (p.assignment.school_class_id if p.assignment else "")),
            "term_id": str(p.term_id),
            "subject_name": p.subject_display,
            "class_name": p.class_display,
            "term_name": p.term.name,
            "author_name": p.author.get_full_name() or p.author.email,
            "status": p.get_status_display(),
            "url": reverse("planning:work_plan_detail", kwargs={"plan_id": p.pk}),
        }
        for p in plans
    ]

    frameworks_meta = [
        {
            "id": str(fw.pk),
            "code": fw.code,
            "name": fw.name,
        }
        for fw in CurriculumFramework.objects.filter(is_active=True).order_by("name")
    ]
    scheme_meta = [
        {
            "id": str(s.pk),
            "title": s.title,
            "subject_code": s.subject_code,
            "subject_name": s.subject_name,
            "syllabus_years": s.syllabus_years or "",
            "year_group": s.year_group or "",
            "display_name": f"{s.subject_code} {s.subject_name} {s.syllabus_years or ''}".strip(),
            "framework_id": str(s.framework_id),
            "framework_code": s.framework.code,
            "framework_name": s.framework.name,
        }
        for s in SchemeOfWork.objects.filter(is_active=True).select_related("framework")
    ]
    term_meta = [
        {
            "id": str(t.pk),
            "name": t.name,
            "academic_year_id": str(t.academic_year_id),
        }
        for t in Term.objects.filter(school=request.school, is_active=True)
    ]

    return render(
        request,
        "planning/work_plan_list.html",
        {
            "form": form,
            "plans": plans,
            "frameworks_meta": frameworks_meta,
            "classes_meta": classes_meta,
            "subjects_meta": subjects_meta,
            "assignment_meta": assignment_meta,
            "existing_plans_meta": existing_plans_meta,
            "scheme_meta": scheme_meta,
            "term_meta": term_meta,
        },
    )


@login_required
@school_required
def work_plan_detail(request, plan_id):
    plan = get_object_or_404(
        WorkPlan.objects.select_related(
            "subject", "school_class", "assignment__subject", "assignment__school_class", "term", "scheme", "author"
        ),
        pk=plan_id,
    )
    weeks = list(
        plan.weeks.select_related("topic", "subtopic", "calendar_week")
        .prefetch_related(
            "objective_selections__objective__topic",
            "objective_selections__objective__subtopic",
            "objective_selections__objective__scheme",
        )
        .order_by("sequence")
    )
    topics = list(Topic.objects.filter(scheme=plan.scheme).order_by("sequence"))
    subtopics = list(
        Subtopic.objects.filter(topic__scheme=plan.scheme)
        .select_related("topic")
        .order_by("sequence")
    )
    objectives = list(
        LearningObjective.objects.filter(scheme=plan.scheme)
        .select_related("topic", "subtopic")
        .order_by("sequence")
    )

    today = timezone.localdate()

    if request.method == "POST":
        action = request.POST.get("action")
        if plan.status == WorkPlan.Status.APPROVED or action == "save_remarks":
            try:
                week_remarks = {
                    str(week.pk): request.POST.get(f"week_{week.pk}_remarks", "") for week in weeks
                }
                met_objective_ids = set(request.POST.getlist("met_objectives"))
                update_work_plan_weekly_reflections(
                    plan=plan,
                    actor=request.user,
                    week_remarks=week_remarks,
                    met_objective_ids=met_objective_ids,
                )
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            else:
                messages.success(request, "Weekly remarks and met objectives updated.")
                return redirect("planning:work_plan_detail", plan_id=plan.pk)
        elif plan.is_editable:
            try:
                updates = []
                for week in weeks:
                    updates.append(
                        {
                            "id": week.pk,
                            "topic_id": request.POST.get(f"week_{week.pk}_topic") or None,
                            "subtopic_id": request.POST.get(f"week_{week.pk}_subtopic") or None,
                            "lessons_per_week": request.POST.get(f"week_{week.pk}_lessons")
                            or (1 if week.is_instructional else 0),
                            "objectives": request.POST.getlist(f"week_{week.pk}_objectives"),
                            "remarks": request.POST.get(f"week_{week.pk}_remarks", ""),
                        }
                    )
                plan = save_work_plan(
                    plan=plan,
                    actor=request.user,
                    revision=int(request.POST["revision"]),
                    resources=request.POST.get("resources", ""),
                    week_updates=updates,
                )
            except (KeyError, TypeError, ValueError, ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            else:
                messages.success(request, "Draft saved.")
                return redirect("planning:work_plan_detail", plan_id=plan.pk)

    curriculum_payload = get_curriculum_coverage_data(plan)
    coverage = curriculum_payload["coverage"]
    reflection_stats = get_work_plan_reflection_stats(plan)

    for week in weeks:
        is_past = bool(week.calendar_week and week.calendar_week.ends_on <= today)
        is_current = bool(
            week.calendar_week
            and week.calendar_week.starts_on <= today
            and week.calendar_week.ends_on > today
        )
        week.is_past_week = is_past
        week.is_current_week = is_current
        week.can_remark = is_past or is_current or (plan.status != WorkPlan.Status.APPROVED)

        week.selected_objective_ids = {
            str(item.objective_id) for item in week.objective_selections.all()
        }

        # Calculate topic frequency within this week to evaluate primary cluster rule (>=3 topic cluster)
        topic_counts = {}
        for sel in week.objective_selections.all():
            if sel.objective and sel.objective.topic_id:
                t_key = str(sel.objective.topic_id)
                topic_counts[t_key] = topic_counts.get(t_key, 0) + 1

        selected_details = []
        for item in week.objective_selections.all():
            obj = item.objective
            obj_topic_id = str(obj.topic_id) if obj and obj.topic_id else None
            week_topic_id = str(week.topic_id) if week.topic_id else None

            # Primary Rule: week's topic matches objective topic AND >= 3 objectives from same topic in this week
            is_primary = bool(
                week_topic_id
                and obj_topic_id
                and obj_topic_id == week_topic_id
                and topic_counts.get(obj_topic_id, 0) >= 3
            )
            is_cross = bool(obj and obj.scheme_id != plan.scheme_id)

            selected_details.append(
                {
                    "id": str(item.objective_id),
                    "selection_id": str(item.pk),
                    "code": item.code_snapshot,
                    "text": item.text_snapshot,
                    "is_met": item.is_met,
                    "met_at": item.met_at,
                    "topic_id": obj_topic_id,
                    "subtopic_id": str(obj.subtopic_id) if obj and obj.subtopic_id else None,
                    "topic_title": obj.topic.title if obj and obj.topic_id else "",
                    "subtopic_title": obj.subtopic.title if obj and obj.subtopic_id else "",
                    "is_primary": is_primary,
                    "is_borrowed": not is_primary,
                    "is_cross_curricular": is_cross,
                    "scheme_subject": obj.scheme.subject_name if obj and is_cross else "",
                    "scheme_title": obj.scheme.title if obj and is_cross else "",
                }
            )
        week.selected_objectives_details = selected_details

    return render(
        request,
        "planning/work_plan_detail.html",
        {
            "plan": plan,
            "weeks": weeks,
            "topics": topics,
            "subtopics": subtopics,
            "objectives": objectives,
            "curriculum_json": curriculum_payload,
            "coverage": coverage,
            "reflection_stats": reflection_stats,
            "today": today,
            "editable": plan.is_editable,
            "can_remark": (
                plan.author == request.user
                and (plan.status == WorkPlan.Status.APPROVED or plan.is_editable)
            ),
        },
    )


@login_required
@school_required
@require_POST
def work_plan_submit(request, plan_id):
    plan = get_object_or_404(WorkPlan.objects, pk=plan_id, author=request.user)
    try:
        transition_work_plan(
            plan=plan,
            actor_membership=request.membership,
            target_status=WorkPlan.Status.SUBMITTED,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Work Plan submitted for review.")
    return redirect("planning:work_plan_detail", plan_id=plan.pk)


@login_required
@school_required
def lesson_plan_list(request):
    if request.membership.role != Membership.Role.TEACHER:
        raise PermissionDenied(
            "Lesson Plan creation is limited to teachers with active assignments."
        )

    today = timezone.localdate()
    now = timezone.localtime()

    # Active Term & Term Weeks
    term = Term.objects.filter(school=request.school, is_active=True).order_by("-starts_on").first()
    active_term = Term.objects.filter(school=request.school, starts_on__lte=today, ends_on__gte=today, is_active=True).first() or term

    term_weeks = []
    selected_week_num = 1
    if active_term:
        term_start = active_term.starts_on
        term_end = active_term.ends_on
        # Compute term weeks (e.g. 1 to 14)
        current_w_start = term_start - timedelta(days=term_start.weekday())
        w_num = 1
        while current_w_start <= term_end:
            w_end = current_w_start + timedelta(days=6)
            is_cur = current_w_start <= today <= w_end
            is_past = w_end < today
            if is_cur:
                selected_week_num = w_num
            term_weeks.append({
                "number": w_num,
                "starts_on": current_w_start,
                "ends_on": w_end,
                "is_current": is_cur,
                "is_past": is_past,
            })
            current_w_start += timedelta(days=7)
            w_num += 1

    # Override selected week via GET param
    req_week = request.GET.get("week")
    if req_week:
        try:
            selected_week_num = int(req_week)
        except ValueError:
            pass

    selected_week_obj = next((w for w in term_weeks if w["number"] == selected_week_num), (term_weeks[0] if term_weeks else None))

    # Teacher schedule slots
    schedule_slots = list(
        TeacherScheduleSlot.objects.filter(
            school=request.school, assignment__teacher=request.user, is_active=True
        )
        .select_related("assignment__subject", "assignment__school_class")
        .order_by("day_of_week", "start_time")
    )

    # Slots metadata for JavaScript auto-fill in modal
    slots_json_data = []
    for s in schedule_slots:
        slots_json_data.append({
            "id": str(s.id),
            "assignment_id": str(s.assignment_id),
            "subject_name": s.assignment.subject.name,
            "class_name": s.assignment.school_class.name,
            "day_of_week": s.day_of_week,
            "day_name": s.get_day_of_week_display(),
            "start_time": s.start_time.strftime("%H:%M"),
            "end_time": s.end_time.strftime("%H:%M"),
            "period_label": s.period_label or f"Period ({s.start_time.strftime('%H:%M')})",
            "room": s.room,
        })

    # Build Weekly Timetable Grid for selected week
    timetable_grid = []
    days_list = [
        {"idx": 0, "name": "Monday"},
        {"idx": 1, "name": "Tuesday"},
        {"idx": 2, "name": "Wednesday"},
        {"idx": 3, "name": "Thursday"},
        {"idx": 4, "name": "Friday"},
    ]
    if any(s.day_of_week == 5 for s in schedule_slots):
        days_list.append({"idx": 5, "name": "Saturday"})

    # Distinct periods/times
    distinct_times = []
    seen_times = set()
    for s in schedule_slots:
        t_key = (s.start_time, s.end_time, s.period_label)
        if t_key not in seen_times:
            seen_times.add(t_key)
            distinct_times.append({
                "start_time": s.start_time,
                "end_time": s.end_time,
                "period_label": s.period_label or f"{s.start_time.strftime('%H:%M')}–{s.end_time.strftime('%H:%M')}",
            })
    distinct_times.sort(key=lambda x: x["start_time"])

    # Map existing LessonPlans for teacher
    all_teacher_plans = list(
        LessonPlan.objects.filter(school=request.school, author=request.user)
        .select_related("assignment__subject", "assignment__school_class", "topic")
    )

    plans_by_key = {}
    for p in all_teacher_plans:
        # Key by (assignment_id, lesson_date) and (schedule_slot_id, lesson_date)
        plans_by_key[(str(p.assignment_id), p.lesson_date)] = p
        if p.schedule_slot_id:
            plans_by_key[(str(p.schedule_slot_id), p.lesson_date)] = p

    # Build weekly grid matrix
    week_start_d = selected_week_obj["starts_on"] if selected_week_obj else today
    total_week_slots = 0
    planned_week_slots = 0
    missed_week_slots = 0
    awaiting_remarks_count = 0

    grid_rows = []
    for dt_info in distinct_times:
        row_cells = []
        for day in days_list:
            matching_slot = next(
                (s for s in schedule_slots if s.day_of_week == day["idx"] and s.start_time == dt_info["start_time"]),
                None
            )
            if matching_slot:
                total_week_slots += 1
                slot_date = week_start_d + timedelta(days=day["idx"])
                slot_end_dt = timezone.make_aware(
                    datetime.combine(slot_date, matching_slot.end_time),
                    timezone.get_current_timezone()
                )
                is_slot_past = slot_end_dt < now

                # Lookup Lesson Plan
                plan = plans_by_key.get((str(matching_slot.id), slot_date)) or plans_by_key.get((str(matching_slot.assignment_id), slot_date))

                slot_state = "EMPTY"
                if plan:
                    planned_week_slots += 1
                    if plan.status == LessonPlan.Status.APPROVED:
                        slot_state = "APPROVED"
                    elif plan.status == LessonPlan.Status.SUBMITTED:
                        slot_state = "SUBMITTED"
                    elif plan.status == LessonPlan.Status.RETURNED:
                        slot_state = "RETURNED"
                    elif is_slot_past and (not plan.notes_remarks or plan.boys_attendance is None):
                        slot_state = "AWAITING_REMARKS"
                        awaiting_remarks_count += 1
                    else:
                        slot_state = "DRAFT_PLANNED"
                else:
                    if is_slot_past:
                        slot_state = "MISSED"
                        missed_week_slots += 1
                    else:
                        slot_state = "PENDING_CREATION"

                row_cells.append({
                    "has_slot": True,
                    "slot": matching_slot,
                    "date": slot_date,
                    "date_str": slot_date.strftime("%d %b"),
                    "plan": plan,
                    "state": slot_state,
                    "is_past": is_slot_past,
                })
            else:
                row_cells.append({"has_slot": False})
        grid_rows.append({
            "period_info": dt_info,
            "cells": row_cells,
        })

    # Calculate Semester Progress & Compliance Score
    total_semester_scheduled_to_date = 0
    on_time_planned_count = 0
    missed_total_count = 0

    if active_term and schedule_slots:
        for tw in term_weeks:
            if tw["starts_on"] <= today:
                for day_idx in range(5):
                    d_date = tw["starts_on"] + timedelta(days=day_idx)
                    for s in schedule_slots:
                        if s.day_of_week == day_idx:
                            s_end_dt = timezone.make_aware(datetime.combine(d_date, s.end_time), timezone.get_current_timezone())
                            if s_end_dt <= now:
                                total_semester_scheduled_to_date += 1
                                p = plans_by_key.get((str(s.id), d_date)) or plans_by_key.get((str(s.assignment_id), d_date))
                                if p and p.status in (LessonPlan.Status.SUBMITTED, LessonPlan.Status.APPROVED, LessonPlan.Status.DRAFT):
                                    on_time_planned_count += 1
                                else:
                                    missed_total_count += 1

    compliance_percent = 100
    if total_semester_scheduled_to_date > 0:
        compliance_percent = round((on_time_planned_count / total_semester_scheduled_to_date) * 100)

    # Form handling
    form = LessonPlanCreateForm(request.POST or None, school=request.school, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            plan = create_lesson_plan(
                school=request.school,
                author=request.user,
                assignment=form.cleaned_data["assignment"],
                academic_year=form.cleaned_data["academic_year"],
                term=form.cleaned_data["term"],
                scheme=form.cleaned_data["scheme"],
                lesson_date=form.cleaned_data["lesson_date"],
                start_time=form.cleaned_data.get("start_time"),
                end_time=form.cleaned_data.get("end_time"),
                schedule_slot=form.cleaned_data.get("schedule_slot"),
                topic=form.cleaned_data["topic"],
                origin=form.cleaned_data["originating_work_plan_week"],
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Lesson Plan draft created successfully.")
            return redirect("planning:lesson_plan_detail", plan_id=plan.pk)

    return render(
        request,
        "planning/lesson_plan_list.html",
        {
            "form": form,
            "plans": all_teacher_plans,
            "active_term": active_term,
            "term_weeks": term_weeks,
            "selected_week": selected_week_obj,
            "selected_week_num": selected_week_num,
            "days_list": days_list,
            "grid_rows": grid_rows,
            "total_week_slots": total_week_slots,
            "planned_week_slots": planned_week_slots,
            "missed_week_slots": missed_week_slots,
            "awaiting_remarks_count": awaiting_remarks_count,
            "compliance_percent": compliance_percent,
            "total_semester_lessons": total_semester_scheduled_to_date,
            "missed_total_count": missed_total_count,
            "slots_json_data": slots_json_data,
            "today": today,
            "now": now,
        },
    )


def lesson_plan_detail(request, plan_id):
    plan = get_object_or_404(
        LessonPlan.objects.select_related(
            "assignment__subject",
            "assignment__school_class",
            "term",
            "scheme",
            "topic",
            "subtopic",
            "originating_work_plan_week__work_plan",
            "originating_work_plan_week__calendar_week",
        ),
        pk=plan_id,
        author=request.user,
    )
    topics = Topic.objects.filter(scheme=plan.scheme)
    subtopics = Subtopic.objects.filter(topic__scheme=plan.scheme)
    available_data = get_available_lesson_plan_objectives(plan)

    if request.method == "POST":
        try:
            lesson_date_raw = request.POST.get("lesson_date")
            lesson_date_val = plan.lesson_date
            if lesson_date_raw:
                if isinstance(lesson_date_raw, str):
                    from django.utils.dateparse import parse_date

                    parsed = parse_date(lesson_date_raw)
                    if parsed:
                        lesson_date_val = parsed
                else:
                    lesson_date_val = lesson_date_raw

            values = {
                "lesson_date": lesson_date_val,
                "topic_id": request.POST.get("topic") or plan.topic_id,
                "subtopic_id": request.POST.get("subtopic") or None,
                "boys_attendance": int(request.POST.get("boys_attendance", plan.boys_attendance or 0)),
                "girls_attendance": int(
                    request.POST.get("girls_attendance", plan.girls_attendance or 0)
                ),
                "main_teaching_activity": request.POST.get("main_teaching_activity", ""),
                "assessment_ideas": request.POST.get("assessment_ideas", ""),
                "notes_remarks": request.POST.get("notes_remarks", ""),
                "resources": request.POST.getlist("resources"),
            }
            plan = save_lesson_plan(
                plan=plan,
                actor=request.user,
                revision=int(request.POST["revision"]),
                values=values,
                objective_ids=request.POST.getlist("objectives"),
            )
        except (KeyError, TypeError, ValueError, ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Lesson Plan draft saved.")
            return redirect("planning:lesson_plan_detail", plan_id=plan.pk)

    plan.selected_objective_ids = {
        str(item.objective_id) for item in plan.objective_selections.all()
    }
    return render(
        request,
        "planning/lesson_plan_detail.html",
        {
            "plan": plan,
            "topics": topics,
            "subtopics": subtopics,
            "editable": plan.is_editable,
            "origin_week": available_data["origin_week"],
            "current_week_objectives": available_data["current_week_objectives"],
            "is_current_week_exhausted": available_data["is_current_week_exhausted"],
            "remaining_current_week_count": available_data["remaining_current_week_count"],
            "borrowable_weeks": available_data["borrowable_weeks"],
        },
    )


@login_required
@school_required
@require_POST
def lesson_plan_submit(request, plan_id):
    plan = get_object_or_404(LessonPlan.objects, pk=plan_id, author=request.user)
    try:
        transition_lesson_plan(
            plan=plan,
            actor_membership=request.membership,
            target_status=LessonPlan.Status.SUBMITTED,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Lesson Plan submitted for review.")
    return redirect("planning:lesson_plan_detail", plan_id=plan.pk)


@login_required
@school_required
@xframe_options_sameorigin
def work_plan_pdf(request, plan_id):
    plan = get_object_or_404(
        WorkPlan.objects.select_related(
            "subject", "school_class", "assignment__subject", "assignment__school_class", "term", "scheme", "academic_year"
        ),
        pk=plan_id,
    )
    pdf_buffer = BytesIO()
    render_work_plan(plan, pdf_buffer)
    pdf_buffer.seek(0)
    response = FileResponse(pdf_buffer, content_type="application/pdf")
    is_download = request.GET.get("download") == "1"
    disposition = "attachment" if is_download else "inline"
    safe_subject = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.subject_display)
    safe_class = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.class_display)
    safe_term = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.term.name)
    filename = f"WorkPlan_{safe_subject}_{safe_class}_{safe_term}.pdf"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@login_required
@school_required
@xframe_options_sameorigin
def lesson_plan_pdf(request, plan_id):
    plan = get_object_or_404(LessonPlan.objects, pk=plan_id)
    if request.membership.role == Membership.Role.TEACHER and plan.author_id != request.user.id:
        raise PermissionDenied("You can download only your own Lesson Plans.")
    pdf_buffer = BytesIO()
    render_lesson_plan(plan, pdf_buffer)
    pdf_buffer.seek(0)
    response = FileResponse(pdf_buffer, content_type="application/pdf")
    is_download = request.GET.get("download") == "1"
    disposition = "attachment" if is_download else "inline"
    safe_subject = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.assignment.subject.name)
    safe_date = str(plan.lesson_date)
    filename = f"LessonPlan_{safe_subject}_{safe_date}.pdf"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@login_required
@school_required
def review_queue(request):
    leadership = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if request.membership.role not in leadership:
        raise PermissionDenied("Only curriculum leadership can access the review queue.")

    work_plans = (
        WorkPlan.objects.filter(
            school=request.school,
            status__in={
                WorkPlan.Status.SUBMITTED,
                WorkPlan.Status.RESUBMITTED,
                WorkPlan.Status.UNDER_REVIEW,
            },
        )
        .select_related("assignment__subject", "assignment__school_class", "author", "term")
        .order_by("submitted_at")
    )

    lesson_plans = (
        LessonPlan.objects.filter(
            school=request.school,
            status__in={
                LessonPlan.Status.SUBMITTED,
                LessonPlan.Status.RESUBMITTED,
                LessonPlan.Status.UNDER_REVIEW,
            },
        )
        .select_related(
            "assignment__subject", "assignment__school_class", "author", "term", "topic"
        )
        .order_by("submitted_at")
    )

    return render(
        request,
        "planning/review_queue.html",
        {
            "work_plans": work_plans,
            "lesson_plans": lesson_plans,
        },
    )


@login_required
@school_required
def review_work_plan(request, plan_id):
    leadership = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if request.membership.role not in leadership:
        raise PermissionDenied("Only curriculum leadership can review Work Plans.")

    plan = get_object_or_404(
        WorkPlan.objects.select_related(
            "assignment__subject", "assignment__school_class", "author", "term", "scheme"
        ),
        pk=plan_id,
        school=request.school,
    )

    if plan.status in {WorkPlan.Status.SUBMITTED, WorkPlan.Status.RESUBMITTED}:
        try:
            transition_work_plan(
                plan=plan,
                actor_membership=request.membership,
                target_status=WorkPlan.Status.UNDER_REVIEW,
            )
        except ValidationError:
            pass

    if request.method == "POST":
        action = request.POST.get("action")
        comment = request.POST.get("comment", "").strip()

        target_status = None
        if action == "approve":
            target_status = WorkPlan.Status.APPROVED
        elif action == "return":
            target_status = WorkPlan.Status.RETURNED

        if target_status:
            try:
                transition_work_plan(
                    plan=plan,
                    actor_membership=request.membership,
                    target_status=target_status,
                    comment=comment,
                )
                messages.success(request, f"Work Plan {plan.get_status_display().lower()}.")
                return redirect("planning:review_queue")
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))

    weeks = list(
        plan.weeks.select_related("topic", "subtopic", "calendar_week")
        .prefetch_related(
            "objective_selections__objective__topic",
            "objective_selections__objective__subtopic",
        )
        .order_by("sequence")
    )
    today = timezone.localdate()
    reflection_stats = get_work_plan_reflection_stats(plan)

    for week in weeks:
        is_past = bool(week.calendar_week and week.calendar_week.ends_on <= today)
        is_current = bool(
            week.calendar_week
            and week.calendar_week.starts_on <= today
            and week.calendar_week.ends_on > today
        )
        week.is_past_week = is_past
        week.is_current_week = is_current

        week.selected_objective_ids = {
            str(item.objective_id) for item in week.objective_selections.all()
        }
        week.selected_objectives_details = [
            {
                "id": str(item.objective_id),
                "code": item.code_snapshot,
                "text": item.text_snapshot,
                "is_met": item.is_met,
                "met_at": item.met_at,
                "topic_id": (
                    str(item.objective.topic_id)
                    if item.objective and item.objective.topic_id
                    else None
                ),
                "subtopic_id": (
                    str(item.objective.subtopic_id)
                    if item.objective and item.objective.subtopic_id
                    else None
                ),
                "topic_title": (
                    item.objective.topic.title if item.objective and item.objective.topic_id else ""
                ),
                "subtopic_title": (
                    item.objective.subtopic.title
                    if item.objective and item.objective.subtopic_id
                    else ""
                ),
            }
            for item in week.objective_selections.all()
        ]

    return render(
        request,
        "planning/review_work_plan.html",
        {
            "plan": plan,
            "weeks": weeks,
            "coverage": calculate_work_plan_coverage(plan),
            "reflection_stats": reflection_stats,
            "today": today,
        },
    )


@login_required
@school_required
def review_lesson_plan(request, plan_id):
    leadership = {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
    if request.membership.role not in leadership:
        raise PermissionDenied("Only curriculum leadership can review Lesson Plans.")

    plan = get_object_or_404(
        LessonPlan.objects.select_related(
            "assignment__subject",
            "assignment__school_class",
            "author",
            "term",
            "scheme",
            "topic",
            "subtopic",
        ),
        pk=plan_id,
        school=request.school,
    )

    if plan.status in {LessonPlan.Status.SUBMITTED, LessonPlan.Status.RESUBMITTED}:
        try:
            transition_lesson_plan(
                plan=plan,
                actor_membership=request.membership,
                target_status=LessonPlan.Status.UNDER_REVIEW,
            )
        except ValidationError:
            pass

    if request.method == "POST":
        action = request.POST.get("action")
        comment = request.POST.get("comment", "").strip()

        target_status = None
        if action == "approve":
            target_status = LessonPlan.Status.APPROVED
        elif action == "return":
            target_status = LessonPlan.Status.RETURNED

        if target_status:
            try:
                transition_lesson_plan(
                    plan=plan,
                    actor_membership=request.membership,
                    target_status=target_status,
                    comment=comment,
                )
                messages.success(request, f"Lesson Plan {plan.get_status_display().lower()}.")
                return redirect("planning:review_queue")
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))

    plan.selected_objective_ids = {
        str(item.objective_id) for item in plan.objective_selections.all()
    }

    return render(
        request,
        "planning/review_lesson_plan.html",
        {
            "plan": plan,
        },
    )


@login_required
@school_required
def timetable_upload(request):
    if request.membership.role != Membership.Role.TEACHER:
        raise PermissionDenied("Timetable upload is limited to active school teachers.")

    if request.method == "POST":
        pdf_file = request.FILES.get("timetable_file")
        if not pdf_file:
            messages.error(request, "Please choose a PDF timetable file to upload.")
            return redirect("planning:timetable_upload")

        if not pdf_file.name.lower().endswith(".pdf"):
            messages.error(request, "The uploaded file must be in PDF format.")
            return redirect("planning:timetable_upload")

        try:
            from apps.schools.timetable_services import parse_timetable_pdf

            file_bytes = pdf_file.read()
            parsed_data = parse_timetable_pdf(file_bytes, school=request.school)

            pdf_file.seek(0)
            timetable = TeacherTimetable.objects.create(
                school=request.school,
                teacher=request.user,
                file=pdf_file,
                file_name=pdf_file.name,
                parsed_data=parsed_data,
                status=TeacherTimetable.Status.PENDING_REVIEW,
            )
            messages.success(
                request,
                f"Timetable parsed successfully! Found {len(parsed_data.get('slots', []))} lesson slots. Please review and confirm your assignments.",
            )
            return redirect("planning:timetable_preview", timetable_id=timetable.id)
        except Exception as exc:
            messages.error(request, f"Error parsing timetable PDF: {exc}")
            return redirect("planning:timetable_upload")

    return render(request, "planning/timetable_upload.html")


@login_required
@school_required
def timetable_preview(request, timetable_id):
    timetable = get_object_or_404(
        TeacherTimetable.objects, pk=timetable_id, teacher=request.user, school=request.school
    )
    parsed_data = timetable.parsed_data or {}
    
    # Group Global Cambridge Schemes by Framework (Primary, Lower Sec, IGCSE, AS/A Level)
    frameworks_data = []
    for fw in CurriculumFramework.objects.filter(is_active=True).order_by("code"):
        schemes = SchemeOfWork.objects.filter(framework=fw, is_active=True)
        distinct_subjs = []
        seen = set()
        for s in schemes.order_by("subject_name"):
            key = (s.subject_code, s.subject_name)
            if key not in seen:
                seen.add(key)
                distinct_subjs.append({
                    "subject_code": s.subject_code,
                    "subject_name": s.subject_name,
                    "framework_name": fw.name,
                    "framework_code": fw.code,
                })
        if distinct_subjs:
            frameworks_data.append({
                "framework": fw,
                "subjects": distinct_subjs,
            })

    school_classes = list(SchoolClass.objects.filter(school=request.school, is_active=True))

    return render(
        request,
        "planning/timetable_preview.html",
        {
            "timetable": timetable,
            "parsed_data": parsed_data,
            "subject_mappings": parsed_data.get("detected_subject_mappings", []),
            "slots": parsed_data.get("slots", []),
            "frameworks_data": frameworks_data,
            "school_classes": school_classes,
        },
    )


@login_required
@school_required
@require_POST
def timetable_confirm(request, timetable_id):
    timetable = get_object_or_404(
        TeacherTimetable.objects, pk=timetable_id, teacher=request.user, school=request.school
    )

    import json
    from apps.schools.timetable_services import commit_teacher_timetable

    try:
        payload_raw = request.POST.get("confirmation_payload")
        if payload_raw:
            data = json.loads(payload_raw)
            subject_mappings = data.get("subject_mappings", {})
            confirmed_slots = data.get("confirmed_slots", [])
        else:
            subject_mappings = {}
            for key, val in request.POST.items():
                if key.startswith("subj_map_"):
                    raw_code = key.replace("subj_map_", "")
                    subj_name = request.POST.get(f"subj_name_{raw_code}", val or raw_code)
                    cam_code = request.POST.get(f"cam_code_{raw_code}", "")
                    subject_mappings[raw_code] = {
                        "subject_name": subj_name,
                        "cambridge_code": cam_code,
                    }

            confirmed_slots = []
            slot_count = int(request.POST.get("slot_count", 0))
            for i in range(slot_count):
                if request.POST.get(f"slot_{i}_deleted") == "1":
                    continue
                confirmed_slots.append(
                    {
                        "day_of_week": int(request.POST.get(f"slot_{i}_day", 0)),
                        "start_time": request.POST.get(f"slot_{i}_start", "08:00"),
                        "end_time": request.POST.get(f"slot_{i}_end", "08:45"),
                        "period_label": request.POST.get(f"slot_{i}_period", f"Period {i+1}"),
                        "class_name": request.POST.get(f"slot_{i}_class", "Grade 7"),
                        "year_group": request.POST.get(f"slot_{i}_year_group", "Stage 7"),
                        "subject_raw": request.POST.get(f"slot_{i}_subject_raw", "SUBJ"),
                        "subject_name": request.POST.get(f"slot_{i}_subject_name", "Subject"),
                        "cambridge_code": request.POST.get(f"slot_{i}_cam_code", ""),
                        "room": request.POST.get(f"slot_{i}_room", ""),
                    }
                )

        result = commit_teacher_timetable(
            timetable=timetable,
            subject_mappings=subject_mappings,
            confirmed_slots=confirmed_slots,
            actor=request.user,
        )

        messages.success(
            request,
            f"Successfully confirmed timetable! Created/verified {len(result['assignments'])} teaching assignments and {result['slots_created_count']} weekly schedule slots.",
        )
        return redirect("planning:lesson_plan_list")

    except Exception as exc:
        messages.error(request, f"Error confirming timetable: {exc}")
        return redirect("planning:timetable_preview", timetable_id=timetable.id)


@login_required
@school_required
def my_timetable(request):
    if request.membership.role != Membership.Role.TEACHER:
        raise PermissionDenied("Weekly timetable view is for teachers.")

    slots = list(
        TeacherScheduleSlot.objects.filter(
            school=request.school,
            assignment__teacher=request.user,
            is_active=True,
        )
        .select_related("assignment__subject", "assignment__school_class", "timetable")
        .order_by("day_of_week", "start_time")
    )

    slots_by_day = {i: [] for i in range(7)}
    for s in slots:
        slots_by_day[s.day_of_week].append(s)

    assignments = list(
        TeacherAssignment.objects.filter(
            school=request.school,
            teacher=request.user,
            is_active=True,
        ).select_related("subject", "school_class")
    )

    days_info = [
        {"index": 0, "name": "Monday", "slots": slots_by_day[0]},
        {"index": 1, "name": "Tuesday", "slots": slots_by_day[1]},
        {"index": 2, "name": "Wednesday", "slots": slots_by_day[2]},
        {"index": 3, "name": "Thursday", "slots": slots_by_day[3]},
        {"index": 4, "name": "Friday", "slots": slots_by_day[4]},
        {"index": 5, "name": "Saturday", "slots": slots_by_day[5]},
        {"index": 6, "name": "Sunday", "slots": slots_by_day[6]},
    ]

    return render(
        request,
        "planning/my_timetable.html",
        {
            "days_info": days_info,
            "total_slots": len(slots),
            "assignments": assignments,
        },
    )


@login_required
@school_required
def api_timetable_slot_lookup(request):
    date_str = request.GET.get("date")
    if not date_str:
        return JsonResponse({"slots": []})

    from django.utils.dateparse import parse_date

    parsed_date = parse_date(date_str)
    if not parsed_date:
        return JsonResponse({"slots": []})

    day_of_week = parsed_date.weekday()
    slots = list(
        TeacherScheduleSlot.objects.filter(
            school=request.school,
            assignment__teacher=request.user,
            day_of_week=day_of_week,
            is_active=True,
        )
        .select_related("assignment__subject", "assignment__school_class")
        .order_by("start_time")
    )

    data = [
        {
            "slot_id": str(s.id),
            "assignment_id": str(s.assignment_id),
            "subject_name": s.assignment.subject.name,
            "subject_code": s.assignment.subject.code,
            "class_name": s.assignment.school_class.name,
            "year_group": s.assignment.school_class.year_group,
            "start_time": s.start_time.strftime("%H:%M"),
            "end_time": s.end_time.strftime("%H:%M"),
            "period_label": s.period_label,
            "room": s.room,
        }
        for s in slots
    ]
    return JsonResponse({"day_of_week": day_of_week, "date": date_str, "slots": data})
