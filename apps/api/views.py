from django.contrib.auth import login, logout
from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, status, throttling
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.serializers import (
    LoginSerializer,
    WorkPlanCreateSerializer,
    WorkPlanSaveSerializer,
    WorkPlanTransitionSerializer,
)
from apps.curriculum.models import SchemeOfWork
from apps.planning.models import WorkPlan
from apps.planning.services import (
    active_assignment_for_user,
    create_work_plan,
    save_work_plan,
    transition_work_plan,
)
from apps.schools.models import AcademicYear, Membership, TeacherAssignment, Term


def user_payload(request):
    membership = getattr(request, "membership", None)
    return {
        "id": request.user.pk,
        "email": request.user.email,
        "full_name": request.user.full_name,
        "active_school": (
            {
                "id": str(membership.school_id),
                "name": membership.school.name,
                "code": membership.school.code,
                "role": membership.role,
                "role_label": membership.get_role_display(),
            }
            if membership
            else None
        ),
        "schools": [
            {"id": str(item.school_id), "name": item.school.name, "role": item.role}
            for item in getattr(request, "school_memberships", ())
        ],
    }


class LoginThrottle(throttling.AnonRateThrottle):
    scope = "login"


class LoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user, backend="apps.accounts.backends.EmailBackend")
        # Tenant middleware resolves schools on the next request.
        return Response(
            {"user": {"id": user.pk, "email": user.email, "full_name": user.full_name}},
            status=status.HTTP_200_OK,
        )


class LogoutAPIView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeAPIView(APIView):
    def get(self, request):
        return Response(user_payload(request))


class MyAssignmentsAPIView(APIView):
    def get(self, request):
        if not getattr(request, "school", None):
            return Response({"results": []})
        today = timezone.localdate()
        assignments = TeacherAssignment.objects.filter(
            teacher=request.user,
            is_active=True,
            effective_from__lte=today,
        ).filter(effective_until__isnull=True) | TeacherAssignment.objects.filter(
            teacher=request.user,
            is_active=True,
            effective_from__lte=today,
            effective_until__gte=today,
        )
        assignments = assignments.select_related("subject", "school_class").distinct()
        data = [
            {
                "id": str(item.pk),
                "subject": {
                    "id": str(item.subject_id),
                    "code": item.subject.code,
                    "name": item.subject.name,
                },
                "class": {
                    "id": str(item.school_class_id),
                    "name": item.school_class.name,
                    "year_group": item.school_class.year_group,
                },
                "effective_from": item.effective_from,
                "effective_until": item.effective_until,
            }
            for item in assignments
        ]
        return Response({"results": data})


def work_plan_payload(plan):
    weeks = plan.weeks.select_related("topic", "calendar_week").prefetch_related(
        "objective_selections__objective"
    )
    return {
        "id": str(plan.pk),
        "status": plan.status,
        "status_label": plan.get_status_display(),
        "revision": plan.revision,
        "revision_token": str(plan.revision_token),
        "assignment_id": str(plan.assignment_id),
        "academic_year_id": str(plan.academic_year_id),
        "term_id": str(plan.term_id),
        "scheme_id": str(plan.scheme_id),
        "resources": plan.resources,
        "weeks": [
            {
                "id": str(week.pk),
                "sequence": week.sequence,
                "month_label": week.month_label,
                "week_label": week.week_label,
                "event_label": week.event_label,
                "is_instructional": week.is_instructional,
                "topic": (
                    {"id": str(week.topic_id), "code": week.topic.code, "title": week.topic.title}
                    if week.topic_id
                    else None
                ),
                "objectives": [
                    {
                        "id": str(item.objective_id),
                        "code": item.code_snapshot,
                        "text": item.text_snapshot,
                    }
                    for item in week.objective_selections.all()
                ],
                "remarks": week.remarks,
            }
            for week in weeks
        ],
    }


class WorkPlanListCreateAPIView(APIView):
    def _plans_for_request(self, request):
        plans = WorkPlan.objects.select_related(
            "assignment__subject", "assignment__school_class", "term", "academic_year", "scheme"
        )
        if request.membership.role == Membership.Role.TEACHER:
            plans = plans.filter(author=request.user)
        return plans

    def get(self, request):
        if not getattr(request, "school", None):
            return Response({"results": []})
        return Response({"results": [work_plan_payload(item) for item in self._plans_for_request(request)]})

    def post(self, request):
        if not getattr(request, "school", None):
            raise Http404
        serializer = WorkPlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        assignment = active_assignment_for_user(
            school=request.school, user=request.user, assignment_id=values["assignment_id"]
        )
        try:
            plan = create_work_plan(
                school=request.school,
                author=request.user,
                assignment=assignment,
                academic_year=AcademicYear.objects.get(pk=values["academic_year_id"]),
                term=Term.objects.get(pk=values["term_id"]),
                scheme=SchemeOfWork.objects.get(pk=values["scheme_id"]),
            )
        except (AcademicYear.DoesNotExist, Term.DoesNotExist, SchemeOfWork.DoesNotExist) as exc:
            raise Http404 from exc
        return Response(work_plan_payload(plan), status=status.HTTP_201_CREATED)


class WorkPlanDetailAPIView(APIView):
    def _get_plan(self, request, plan_id):
        try:
            plan = WorkPlan.objects.get(pk=plan_id)
        except WorkPlan.DoesNotExist as exc:
            raise Http404 from exc
        if request.membership.role == Membership.Role.TEACHER and plan.author_id != request.user.id:
            raise Http404
        return plan

    def get(self, request, plan_id):
        return Response(work_plan_payload(self._get_plan(request, plan_id)))

    def patch(self, request, plan_id):
        plan = self._get_plan(request, plan_id)
        serializer = WorkPlanSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        plan = save_work_plan(
            plan=plan,
            actor=request.user,
            revision=values["revision"],
            resources=values.get("resources", ""),
            week_updates=values["weeks"],
        )
        return Response(work_plan_payload(plan))


class WorkPlanTransitionAPIView(WorkPlanDetailAPIView):
    target_status = None

    def post(self, request, plan_id):
        serializer = WorkPlanTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = transition_work_plan(
            plan=self._get_plan(request, plan_id),
            actor_membership=request.membership,
            target_status=self.target_status,
            comment=serializer.validated_data.get("comment", ""),
        )
        return Response(work_plan_payload(plan))


class WorkPlanSubmitAPIView(WorkPlanTransitionAPIView):
    target_status = WorkPlan.Status.SUBMITTED


class WorkPlanReviewAPIView(WorkPlanTransitionAPIView):
    target_status = WorkPlan.Status.UNDER_REVIEW


class WorkPlanReturnAPIView(WorkPlanTransitionAPIView):
    target_status = WorkPlan.Status.RETURNED


class WorkPlanApproveAPIView(WorkPlanTransitionAPIView):
    target_status = WorkPlan.Status.APPROVED
