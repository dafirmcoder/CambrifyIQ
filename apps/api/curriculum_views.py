"""Scoped curriculum and template metadata endpoints (plan section 14).

Every endpoint resolves options through ``apps.curriculum.services`` or
``apps.planning.services`` so the API can never return a value the caller is not
authorised to select.
"""

from django.core.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.curriculum import services as curriculum_services
from apps.planning import services as planning_services
from apps.planning.models import PlanType


class TenantAPIView(APIView):
    """Base view requiring an active school membership."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.membership = getattr(request, "membership", None)
        if not self.membership:
            raise PermissionDenied("Select an active school first.")


def _objective_payload(objective):
    return {
        "id": str(objective.pk),
        "code": objective.code,
        "text": objective.text,
        "label": objective.label,
        "subtopic_id": str(objective.subtopic_id) if objective.subtopic_id else None,
    }


class SchemeListAPIView(TenantAPIView):
    """GET /api/schemes/ — schemes the caller may plan against."""

    def get(self, request):
        schemes = curriculum_services.visible_schemes(self.membership)
        return Response(
            {
                "results": [
                    {
                        "id": str(scheme.pk),
                        "code": scheme.code,
                        "title": scheme.title,
                        "version": scheme.version,
                        "subject": {
                            "id": str(scheme.subject_id),
                            "code": scheme.subject.code,
                            "name": scheme.subject.name,
                        },
                        "class": {
                            "id": str(scheme.school_class_id),
                            "name": scheme.school_class.name,
                        },
                        "academic_year": scheme.academic_year.name,
                        "term": scheme.term.name if scheme.term_id else None,
                    }
                    for scheme in schemes
                ]
            }
        )


class SchemeObjectiveAPIView(TenantAPIView):
    """GET /api/schemes/{id}/objectives/ — the unit, sub-unit and LO tree."""

    def get(self, request, scheme_id):
        topics = curriculum_services.topic_options(self.membership, scheme_id)
        results = []
        for topic in topics:
            subtopics = curriculum_services.subtopic_options(self.membership, topic.pk)
            results.append(
                {
                    "id": str(topic.pk),
                    "code": topic.code,
                    "title": topic.title,
                    "sequence": topic.sequence,
                    "subtopics": [
                        {
                            "id": str(subtopic.pk),
                            "code": subtopic.code,
                            "title": subtopic.title,
                            "objectives": [
                                _objective_payload(objective)
                                for objective in curriculum_services.learning_objective_options(
                                    self.membership, subtopic_id=subtopic.pk
                                )
                            ],
                        }
                        for subtopic in subtopics
                    ],
                    "objectives": [
                        _objective_payload(objective)
                        for objective in curriculum_services.learning_objective_options(
                            self.membership, topic_id=topic.pk
                        )
                    ],
                }
            )
        return Response({"results": results})


class TemplateAPIView(TenantAPIView):
    """GET /api/templates/?type={type} — the current template for a plan type."""

    def get(self, request):
        plan_type = request.query_params.get("type", PlanType.LESSON_PLAN)
        if plan_type not in PlanType.values:
            return Response(
                {"detail": "Unknown template type."}, status=status.HTTP_400_BAD_REQUEST
            )
        version = planning_services.current_version(
            school=self.membership.school, plan_type=plan_type
        )
        if version is None:
            return Response(
                {"detail": "No template version is published for this plan type yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "id": str(version.pk),
                "plan_type": plan_type,
                "version": version.version,
                "status": version.status,
                "page_count": version.page_count,
                "renderable": version.is_renderable,
            }
        )


class TemplateFieldAPIView(TenantAPIView):
    """GET /api/templates/{id}/fields/ — field map, boxes and validation."""

    def get(self, request, version_id):
        from apps.planning.models import TemplateVersion

        version = TemplateVersion.objects.filter(pk=version_id).select_related("template").first()
        if version is None:
            return Response(
                {"detail": "That template version is not available."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(planning_services.field_map_payload(version))
