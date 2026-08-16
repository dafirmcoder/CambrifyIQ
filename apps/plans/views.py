"""Plan builder, review and PDF views (plan sections 7.2, 7.3, 7.5, 13)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.curriculum.services import (
    learning_objective_options,
    topic_options,
    visible_schemes,
)
from apps.plans import pdf as pdf_renderer
from apps.plans import services, validation, workflow
from apps.plans.models import LessonPlan, PlanState, WorkPlan, WorkPlanRow
from apps.schools.models import Membership, TeacherAssignment, Term


def _membership(request):
    membership = getattr(request, "membership", None)
    if membership is None:
        raise PermissionDenied("Select an active school first.")
    return membership


def _get_plan(request, model, plan_id):
    plan = get_object_or_404(
        model.objects.select_related(
            "assignment__subject", "assignment__school_class", "template_version", "author"
        ),
        pk=plan_id,
    )
    workflow.assert_can_view(_membership(request), plan)
    return plan


@login_required
@school_required
def plan_list(request):
    """Every plan the caller may see, split by type."""
    membership = _membership(request)
    work_plans = services.visible_plans(membership, WorkPlan).select_related("term")
    lesson_plans = services.visible_plans(membership, LessonPlan)
    context = {
        "work_plans": work_plans[:50],
        "lesson_plans": lesson_plans[:50],
        "is_teacher": membership.role == Membership.Role.TEACHER,
        "assignments": TeacherAssignment.objects.for_school(membership.school_id)
        .filter(teacher_id=membership.user_id, is_active=True)
        .select_related("subject", "school_class"),
        "terms": Term.objects.select_related("academic_year").filter(is_active=True),
    }
    return render(request, "plans/list.html", context)


@login_required
@school_required
@require_POST
def create_work_plan(request):
    membership = _membership(request)
    term = get_object_or_404(Term.objects.all(), pk=request.POST.get("term"))
    try:
        plan = services.create_work_plan(
            membership=membership,
            assignment_id=request.POST.get("assignment"),
            term=term,
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
        return redirect("plans:list")
    messages.success(request, "Work plan created. All calendar weeks are ready.")
    return redirect("plans:work_plan", plan_id=plan.pk)


@login_required
@school_required
def work_plan_detail(request, plan_id):
    plan = _get_plan(request, WorkPlan, plan_id)
    membership = _membership(request)
    rows = list(services.week_rows_for(plan))
    schemes = list(visible_schemes(membership))

    # Objective options are resolved once per scheme and attached to each row, so
    # the picker can never offer a value outside the caller's assignment scope.
    options = []
    for scheme in schemes:
        for topic in topic_options(membership, scheme.pk):
            options.extend(learning_objective_options(membership, topic_id=topic.pk))
    selected = {
        row.pk: {str(pk) for pk in row.learning_objectives.values_list("pk", flat=True)}
        for row in rows
    }
    for row in rows:
        row.available_objectives = options
        row.selected_objective_ids = selected.get(row.pk, set())

    context = {
        "plan": plan,
        "rows": rows,
        "schemes": schemes,
        "issues": validation.work_plan_issues(plan),
        "history": workflow.history(plan)[:20],
        "actions": workflow.transitions_for(membership, plan),
        "is_author": plan.author_id == membership.user_id,
        "can_edit": plan.is_editable and plan.author_id == membership.user_id,
    }
    return render(request, "plans/work_plan.html", context)


@login_required
@school_required
@require_POST
def save_work_plan_row(request, plan_id, row_id):
    """Autosave endpoint for one week row."""
    plan = _get_plan(request, WorkPlan, plan_id)
    membership = _membership(request)
    row = get_object_or_404(WorkPlanRow.objects.all(), pk=row_id, work_plan=plan)

    objective_ids = request.POST.getlist("objectives") or None
    if "objectives" in request.POST and not request.POST.getlist("objectives"):
        objective_ids = []

    try:
        services.save_work_plan_row(
            membership=membership,
            plan=plan,
            row=row,
            base_revision=request.POST.get("revision") or None,
            objective_ids=objective_ids,
            remarks=request.POST.get("remarks"),
        )
    except services.RevisionConflict as conflict:
        return JsonResponse(
            {"ok": False, "conflict": True, "detail": conflict.messages}, status=409
        )
    except (ValidationError, PermissionDenied) as error:
        detail = error.messages if isinstance(error, ValidationError) else [str(error)]
        return JsonResponse({"ok": False, "detail": detail}, status=400)

    row.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "revision": plan.revision,
            "objective_labels": row.objective_labels,
            "saved_at": timezone.localtime().strftime("%H:%M:%S"),
        }
    )


@login_required
@school_required
@require_POST
def save_work_plan_resources(request, plan_id):
    plan = _get_plan(request, WorkPlan, plan_id)
    try:
        services.save_work_plan_resources(
            membership=_membership(request),
            plan=plan,
            resources=request.POST.get("resources", ""),
            base_revision=request.POST.get("revision") or None,
        )
    except services.RevisionConflict as conflict:
        return JsonResponse(
            {"ok": False, "conflict": True, "detail": conflict.messages}, status=409
        )
    except (ValidationError, PermissionDenied) as error:
        detail = error.messages if isinstance(error, ValidationError) else [str(error)]
        return JsonResponse({"ok": False, "detail": detail}, status=400)
    return JsonResponse({"ok": True, "revision": plan.revision})


@login_required
@school_required
@require_POST
def create_lesson_plan(request):
    membership = _membership(request)
    row = None
    if row_id := request.POST.get("work_plan_row"):
        row = get_object_or_404(WorkPlanRow.objects.all(), pk=row_id)

    lesson_date = request.POST.get("lesson_date") or timezone.localdate().isoformat()
    try:
        plan = services.create_lesson_plan(
            membership=membership,
            assignment_id=request.POST.get("assignment"),
            lesson_date=lesson_date,
            work_plan_row=row,
        )
    except (ValidationError, PermissionDenied) as error:
        detail = error.messages if isinstance(error, ValidationError) else [str(error)]
        messages.error(request, "; ".join(detail))
        return redirect("plans:list")
    messages.success(request, "Lesson plan created.")
    return redirect("plans:lesson_plan", plan_id=plan.pk)


@login_required
@school_required
def lesson_plan_detail(request, plan_id):
    plan = _get_plan(request, LessonPlan, plan_id)
    membership = _membership(request)

    schemes = list(visible_schemes(membership))
    topics = []
    for scheme in schemes:
        topics.extend(topic_options(membership, scheme.pk))

    objectives = []
    if plan.topic_id:
        objectives = list(
            learning_objective_options(
                membership, topic_id=plan.topic_id, subtopic_id=plan.subtopic_id
            )
        )

    school_class = plan.assignment.school_class
    context = {
        "plan": plan,
        "schemes": schemes,
        "topics": topics,
        "objectives": objectives,
        "selected_objective_ids": {
            str(pk) for pk in plan.learning_objectives.values_list("pk", flat=True)
        },
        "issues": validation.lesson_plan_issues(plan),
        "warnings": validation.overflow_warnings(plan),
        "guidance": validation.text_guidance(plan.template_version),
        "history": workflow.history(plan)[:20],
        "actions": workflow.transitions_for(membership, plan),
        "boys_max": school_class.boys_count,
        "girls_max": school_class.girls_count,
        "is_author": plan.author_id == membership.user_id,
        "can_edit": plan.is_editable and plan.author_id == membership.user_id,
    }
    return render(request, "plans/lesson_plan.html", context)


@login_required
@school_required
@require_POST
def save_lesson_plan(request, plan_id):
    plan = _get_plan(request, LessonPlan, plan_id)
    membership = _membership(request)

    def number(name):
        raw = request.POST.get(name)
        if raw in (None, ""):
            return ...
        try:
            return int(raw)
        except ValueError:
            return ...

    objective_ids = request.POST.getlist("objectives") if "objectives" in request.POST else None

    try:
        services.save_lesson_plan(
            membership=membership,
            plan=plan,
            base_revision=request.POST.get("revision") or None,
            subtopic_id=request.POST.get("subtopic") if "subtopic" in request.POST else ...,
            objective_ids=objective_ids,
            boys_present=number("boys_present"),
            girls_present=number("girls_present"),
            main_teaching_activity=request.POST.get("main_teaching_activity"),
            assessment_ideas=request.POST.get("assessment_ideas"),
            notes_remarks=request.POST.get("notes_remarks"),
            attendance_exception=request.POST.get("attendance_exception"),
        )
    except services.RevisionConflict as conflict:
        return JsonResponse(
            {"ok": False, "conflict": True, "detail": conflict.messages}, status=409
        )
    except (ValidationError, PermissionDenied) as error:
        detail = error.messages if isinstance(error, ValidationError) else [str(error)]
        return JsonResponse({"ok": False, "detail": detail}, status=400)

    plan.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "revision": plan.revision,
            "attendance_total": plan.attendance_total,
            "issues": validation.lesson_plan_issues(plan),
            "warnings": validation.overflow_warnings(plan),
            "saved_at": timezone.localtime().strftime("%H:%M:%S"),
        }
    )


PLAN_MODELS = {"work": WorkPlan, "lesson": LessonPlan}


@login_required
@school_required
@require_POST
def transition(request, kind, plan_id, action):
    """Run one workflow action (7.5)."""
    model = PLAN_MODELS.get(kind)
    if model is None:
        raise PermissionDenied("Unknown plan type.")
    plan = _get_plan(request, model, plan_id)
    membership = _membership(request)
    comment = request.POST.get("comment", "")

    handlers = {
        "submit": lambda: workflow.submit(membership=membership, plan=plan, comment=comment),
        "claim": lambda: workflow.claim_review(membership=membership, plan=plan, comment=comment),
        "return": lambda: workflow.return_for_changes(
            membership=membership, plan=plan, comment=comment
        ),
        "approve": lambda: workflow.approve(membership=membership, plan=plan, comment=comment),
        "archive": lambda: workflow.archive(membership=membership, plan=plan, comment=comment),
    }
    handler = handlers.get(action)
    if handler is None:
        raise PermissionDenied("Unknown workflow action.")

    try:
        handler()
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except PermissionDenied as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Plan {action}d.")

    target = "plans:work_plan" if kind == "work" else "plans:lesson_plan"
    return redirect(target, plan_id=plan.pk)


@login_required
@school_required
def plan_pdf(request, kind, plan_id):
    """Preview or download the approved-format PDF (8.6)."""
    model = PLAN_MODELS.get(kind)
    if model is None:
        raise PermissionDenied("Unknown plan type.")
    plan = _get_plan(request, model, plan_id)

    try:
        content, file_name, _ = pdf_renderer.generate_document(plan)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        target = "plans:work_plan" if kind == "work" else "plans:lesson_plan"
        return redirect(target, plan_id=plan.pk)

    disposition = "attachment" if request.GET.get("download") else "inline"
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{file_name}"'
    return response


@login_required
@school_required
def review_queue(request):
    """Leadership approval queue (13.4)."""
    membership = _membership(request)
    if membership.role == Membership.Role.TEACHER:
        raise PermissionDenied("Only leadership can review plans.")
    context = {
        "work_plans": services.review_queue(membership, WorkPlan).select_related("term"),
        "lesson_plans": services.review_queue(membership, LessonPlan),
        "returned": services.visible_plans(membership, LessonPlan).filter(state=PlanState.RETURNED)[
            :10
        ],
    }
    return render(request, "plans/review_queue.html", context)
