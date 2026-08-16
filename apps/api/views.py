from django.contrib.auth import login, logout
from django.utils import timezone
from rest_framework import permissions, status, throttling
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.serializers import LoginSerializer
from apps.schools.models import TeacherAssignment


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
