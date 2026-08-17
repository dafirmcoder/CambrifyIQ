from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.curriculum.models import LearningObjective, Subtopic, Topic
from apps.planning.forms import LessonPlanCreateForm, WorkPlanCreateForm
from apps.planning.models import LessonPlan, WorkPlan
from apps.planning.pdf import render_work_plan
from apps.planning.services import (
    create_lesson_plan,
    create_work_plan,
    save_lesson_plan,
    save_work_plan,
    transition_lesson_plan,
    transition_work_plan,
)
from apps.schools.models import Membership


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
        "assignment__subject", "assignment__school_class", "term"
    )
    return render(request, "planning/work_plan_list.html", {"form": form, "plans": plans})


@login_required
@school_required
def work_plan_detail(request, plan_id):
    plan = get_object_or_404(
        WorkPlan.objects.select_related("assignment__subject", "assignment__school_class", "term", "scheme"),
        pk=plan_id,
        author=request.user,
    )
    weeks = list(plan.weeks.select_related("topic").prefetch_related("objective_selections"))
    topics = Topic.objects.filter(scheme=plan.scheme)
    objectives = LearningObjective.objects.filter(scheme=plan.scheme).select_related("topic")
    if request.method == "POST":
        try:
            updates = []
            for week in weeks:
                updates.append(
                    {
                        "id": week.pk,
                        "topic_id": request.POST.get(f"week_{week.pk}_topic") or None,
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
    for week in weeks:
        week.selected_objective_ids = {str(item.objective_id) for item in week.objective_selections.all()}
    return render(
        request,
        "planning/work_plan_detail.html",
        {
            "plan": plan,
            "weeks": weeks,
            "topics": topics,
            "objectives": objectives,
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
        raise PermissionDenied("Lesson Plan creation is limited to teachers with active assignments.")
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
    objectives = LearningObjective.objects.filter(scheme=plan.scheme).select_related("topic", "subtopic")
    if request.method == "POST":
        try:
            values = {
                "lesson_date": request.POST.get("lesson_date", plan.lesson_date),
                "topic_id": request.POST.get("topic") or plan.topic_id,
                "subtopic_id": request.POST.get("subtopic") or None,
                "boys_attendance": int(request.POST.get("boys_attendance", plan.boys_attendance)),
                "girls_attendance": int(request.POST.get("girls_attendance", plan.girls_attendance)),
                "main_teaching_activity": request.POST.get("main_teaching_activity", ""),
                "assessment_ideas": request.POST.get("assessment_ideas", ""),
                "notes_remarks": request.POST.get("notes_remarks", ""),
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
    plan.selected_objective_ids = {str(item.objective_id) for item in plan.objective_selections.all()}
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
