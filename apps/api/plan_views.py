"""Plan, workflow, sync and dashboard API endpoints (plan section 14)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from apps.api.curriculum_views import TenantAPIView
from apps.dashboard import reporting
from apps.plans import pdf as pdf_renderer
from apps.plans import services, sync, validation, workflow
from apps.plans.models import LessonPlan, WorkPlan
from apps.schools.models import Term

PLAN_MODELS = {"work-plans": WorkPlan, "lesson-plans": LessonPlan}


def plan_payload(plan, *, detail=False):
    data = {
        "id": str(plan.pk),
        "state": plan.state,
        "state_label": plan.get_state_display(),
        "revision": plan.revision,
        "editable": plan.is_editable,
        "locked": plan.is_locked,
        "author": plan.author.get_short_name(),
        "subject": plan.assignment.subject.name,
        "class": plan.assignment.school_class.name,
        "template_version": plan.template_version.version,
        "updated_at": plan.updated_at,
    }
    if isinstance(plan, LessonPlan):
        data.update(
            {
                "lesson_date": plan.lesson_date,
                "boys_present": plan.boys_present,
                "girls_present": plan.girls_present,
                "attendance_total": plan.attendance_total,
                "objectives": plan.objective_labels,
            }
        )
    else:
        data.update({"term": plan.term.name, "academic_year": plan.academic_year.name})
    if detail:
        data["issues"] = validation.plan_issues(plan)
        data["warnings"] = validation.overflow_warnings(plan)
        data["actions"] = (
            workflow.transitions_for(plan._membership, plan) if hasattr(plan, "_membership") else []
        )
    return data


class PlanListAPIView(TenantAPIView):
    """GET/POST /api/{work-plans|lesson-plans}/"""

    def get(self, request, kind):
        model = PLAN_MODELS.get(kind)
        if model is None:
            return Response({"detail": "Unknown plan type."}, status=status.HTTP_404_NOT_FOUND)
        plans = services.visible_plans(self.membership, model)
        return Response({"results": [plan_payload(plan) for plan in plans[:100]]})

    def post(self, request, kind):
        model = PLAN_MODELS.get(kind)
        if model is None:
            return Response({"detail": "Unknown plan type."}, status=status.HTTP_404_NOT_FOUND)
        try:
            if model is WorkPlan:
                term = get_object_or_404(Term.objects.all(), pk=request.data.get("term"))
                plan = services.create_work_plan(
                    membership=self.membership,
                    assignment_id=request.data.get("assignment"),
                    term=term,
                )
            else:
                plan = services.create_lesson_plan(
                    membership=self.membership,
                    assignment_id=request.data.get("assignment"),
                    lesson_date=request.data.get("lesson_date"),
                )
        except ValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(plan_payload(plan), status=status.HTTP_201_CREATED)


class PlanDetailAPIView(TenantAPIView):
    """GET/PATCH /api/{kind}/{id}/ — read or autosave with a revision token."""

    def get_plan(self, kind, plan_id):
        model = PLAN_MODELS.get(kind)
        if model is None:
            raise PermissionDenied("Unknown plan type.")
        plan = get_object_or_404(model.objects.all(), pk=plan_id)
        workflow.assert_can_view(self.membership, plan)
        plan._membership = self.membership
        return plan

    def get(self, request, kind, plan_id):
        plan = self.get_plan(kind, plan_id)
        return Response(plan_payload(plan, detail=True))

    def patch(self, request, kind, plan_id):
        plan = self.get_plan(kind, plan_id)
        data = request.data
        try:
            if isinstance(plan, LessonPlan):
                services.save_lesson_plan(
                    membership=self.membership,
                    plan=plan,
                    base_revision=data.get("revision"),
                    subtopic_id=data.get("subtopic_id", ...),
                    objective_ids=data.get("objective_ids"),
                    boys_present=data.get("boys_present", ...),
                    girls_present=data.get("girls_present", ...),
                    main_teaching_activity=data.get("main_teaching_activity"),
                    assessment_ideas=data.get("assessment_ideas"),
                    notes_remarks=data.get("notes_remarks"),
                    attendance_exception=data.get("attendance_exception"),
                )
            else:
                services.save_work_plan_resources(
                    membership=self.membership,
                    plan=plan,
                    resources=data.get("resources", ""),
                    base_revision=data.get("revision"),
                )
        except services.RevisionConflict as conflict:
            return Response(
                {"detail": conflict.messages, "conflict": True, "revision": plan.revision},
                status=status.HTTP_409_CONFLICT,
            )
        except ValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(plan_payload(plan, detail=True))


class PlanActionAPIView(TenantAPIView):
    """POST /api/{kind}/{id}/{action}/ — one workflow transition."""

    HANDLERS = {
        "submit": workflow.submit,
        "review": workflow.claim_review,
        "return": workflow.return_for_changes,
        "approve": workflow.approve,
        "archive": workflow.archive,
    }

    def post(self, request, kind, plan_id, action):
        model = PLAN_MODELS.get(kind)
        handler = self.HANDLERS.get(action)
        if model is None or handler is None:
            return Response({"detail": "Unknown action."}, status=status.HTTP_404_NOT_FOUND)

        plan = get_object_or_404(model.objects.all(), pk=plan_id)
        workflow.assert_can_view(self.membership, plan)
        kwargs = {
            "membership": self.membership,
            "plan": plan,
            "comment": request.data.get("comment", ""),
        }
        try:
            handler(**kwargs)
        except ValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        plan._membership = self.membership
        return Response(plan_payload(plan, detail=True))


class PlanPdfAPIView(TenantAPIView):
    """GET /api/{kind}/{id}/pdf/"""

    def get(self, request, kind, plan_id):
        model = PLAN_MODELS.get(kind)
        if model is None:
            return Response({"detail": "Unknown plan type."}, status=status.HTTP_404_NOT_FOUND)
        plan = get_object_or_404(model.objects.all(), pk=plan_id)
        workflow.assert_can_view(self.membership, plan)
        try:
            content, file_name, _ = pdf_renderer.generate_document(plan)
        except ValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_409_CONFLICT)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{file_name}"'
        return response


class SyncAPIView(TenantAPIView):
    """POST /api/sync/operations/ — replay a queued offline batch."""

    def post(self, request):
        operations = request.data.get("operations")
        if not isinstance(operations, list):
            return Response(
                {"detail": "Provide an 'operations' list."}, status=status.HTTP_400_BAD_REQUEST
            )
        results = sync.apply_batch(membership=self.membership, operations=operations)
        conflicts = [item for item in results if item.get("result") == "conflict"]
        return Response(
            {"results": results, "conflicts": len(conflicts)},
            status=status.HTTP_207_MULTI_STATUS if conflicts else status.HTTP_200_OK,
        )


class DashboardAPIView(TenantAPIView):
    """GET /api/dashboard/{role}/ — role KPIs."""

    def get(self, request, role):
        if role != self.membership.role:
            raise PermissionDenied("You can only read your own role dashboard.")
        context = reporting.dashboard_context(self.membership)
        payload = {
            "role": role,
            "lesson_states": context["lesson_states"],
            "work_states": context["work_states"],
            "coverage": [
                {
                    "subject": row["subject"],
                    "class": row["class_name"],
                    "teacher": row["teacher"],
                    "planned": row["planned"],
                    "available": row["available"],
                    "percent": row["percent"],
                }
                for row in context.get("coverage", [])
            ],
        }
        for key in ("summary", "pending", "completion", "turnaround", "content"):
            if key in context:
                payload[key] = context[key]
        return Response(payload)
