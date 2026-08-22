from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.curriculum.models import LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.planning.forms import LessonPlanCreateForm, WorkPlanCreateForm
from apps.planning.models import LessonPlan, WorkPlan
from apps.planning.pdf import render_lesson_plan, render_work_plan
from apps.planning.services import (
    calculate_work_plan_coverage,
    create_lesson_plan,
    create_work_plan,
    get_curriculum_coverage_data,
    save_lesson_plan,
    save_work_plan,
    transition_lesson_plan,
    transition_work_plan,
)
from apps.schools.models import Membership, TeacherAssignment, Term


@login_required
@school_required
def work_plan_list(request):
    if request.membership.role != Membership.Role.TEACHER:
        raise PermissionDenied("Work Plan creation is limited to teachers with active assignments.")
    form = WorkPlanCreateForm(request.POST or None, school=request.school, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            plan = create_work_plan(
                school=request.school,
                author=request.user,
                assignment=form.cleaned_data["assignment"],
                academic_year=form.cleaned_data["academic_year"],
                term=form.cleaned_data["term"],
                scheme=form.cleaned_data["scheme"],
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Work Plan created from the current school calendar.")
            return redirect("planning:work_plan_detail", plan_id=plan.pk)

    plans = WorkPlan.objects.filter(author=request.user).select_related(
        "assignment__subject", "assignment__school_class", "term", "scheme"
    )

    from django.db import models
    from django.utils import timezone

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

    assignment_meta = [
        {
            "id": str(a.pk),
            "subject_name": a.subject.name,
            "subject_code": a.subject.cambridge_code or a.subject.code,
            "class_name": a.school_class.name,
            "year_group": a.school_class.year_group or "",
        }
        for a in teacher_assignments
    ]
    scheme_meta = [
        {
            "id": str(s.pk),
            "title": s.title,
            "subject_code": s.subject_code,
            "year_group": s.year_group or "",
        }
        for s in SchemeOfWork.objects.filter(is_active=True)
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
            "assignment_meta": assignment_meta,
            "scheme_meta": scheme_meta,
            "term_meta": term_meta,
        },
    )


@login_required
@school_required
def work_plan_detail(request, plan_id):
    plan = get_object_or_404(
        WorkPlan.objects.select_related(
            "assignment__subject", "assignment__school_class", "term", "scheme"
        ),
        pk=plan_id,
        author=request.user,
    )
    weeks = list(
        plan.weeks.select_related("topic", "subtopic", "calendar_week")
        .prefetch_related(
            "objective_selections__objective__topic",
            "objective_selections__objective__subtopic",
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

    if request.method == "POST":
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

    for week in weeks:
        week.selected_objective_ids = {
            str(item.objective_id) for item in week.objective_selections.all()
        }
        week.selected_objectives_details = [
            {
                "id": str(item.objective_id),
                "code": item.code_snapshot,
                "text": item.text_snapshot,
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
        "planning/work_plan_detail.html",
        {
            "plan": plan,
            "weeks": weeks,
            "topics": topics,
            "subtopics": subtopics,
            "objectives": objectives,
            "curriculum_json": curriculum_payload,
            "coverage": coverage,
            "editable": plan.is_editable,
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
                topic=form.cleaned_data["topic"],
                origin=form.cleaned_data["originating_work_plan_week"],
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Lesson Plan created from the selected teaching context.")
            return redirect("planning:lesson_plan_detail", plan_id=plan.pk)
    plans = LessonPlan.objects.filter(author=request.user).select_related(
        "assignment__subject", "assignment__school_class", "term", "topic"
    )
    return render(request, "planning/lesson_plan_list.html", {"form": form, "plans": plans})


@login_required
@school_required
def lesson_plan_detail(request, plan_id):
    plan = get_object_or_404(
        LessonPlan.objects.select_related(
            "assignment__subject",
            "assignment__school_class",
            "term",
            "scheme",
            "topic",
            "subtopic",
        ),
        pk=plan_id,
        author=request.user,
    )
    topics = Topic.objects.filter(scheme=plan.scheme)
    subtopics = Subtopic.objects.filter(topic__scheme=plan.scheme)
    objectives = LearningObjective.objects.filter(scheme=plan.scheme).select_related(
        "topic", "subtopic"
    )
    if request.method == "POST":
        try:
            values = {
                "lesson_date": request.POST.get("lesson_date", plan.lesson_date),
                "topic_id": request.POST.get("topic") or plan.topic_id,
                "subtopic_id": request.POST.get("subtopic") or None,
                "boys_attendance": int(request.POST.get("boys_attendance", plan.boys_attendance)),
                "girls_attendance": int(
                    request.POST.get("girls_attendance", plan.girls_attendance)
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
            "objectives": objectives,
            "editable": plan.is_editable,
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
def work_plan_pdf(request, plan_id):
    plan = get_object_or_404(WorkPlan.objects, pk=plan_id)
    if request.membership.role == Membership.Role.TEACHER and plan.author_id != request.user.id:
        raise PermissionDenied("You can download only your own Work Plans.")
    pdf_buffer = BytesIO()
    render_work_plan(plan, pdf_buffer)
    pdf_buffer.seek(0)
    response = FileResponse(pdf_buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="work-plan-{plan.pk}.pdf"'
    return response


@login_required
@school_required
def lesson_plan_pdf(request, plan_id):
    plan = get_object_or_404(LessonPlan.objects, pk=plan_id)
    if request.membership.role == Membership.Role.TEACHER and plan.author_id != request.user.id:
        raise PermissionDenied("You can download only your own Lesson Plans.")
    pdf_buffer = BytesIO()
    render_lesson_plan(plan, pdf_buffer)
    pdf_buffer.seek(0)
    response = FileResponse(pdf_buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="lesson-plan-{plan.pk}.pdf"'
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
    for week in weeks:
        week.selected_objective_ids = {
            str(item.objective_id) for item in week.objective_selections.all()
        }
        week.selected_objectives_details = [
            {
                "id": str(item.objective_id),
                "code": item.code_snapshot,
                "text": item.text_snapshot,
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
