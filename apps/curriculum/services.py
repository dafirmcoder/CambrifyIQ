"""Authorised curriculum option services.

Section 10.5 of the plan requires defence in depth: "A value that cannot be used
in a plan also cannot be discovered through a dropdown endpoint." Every option
list offered to a builder must therefore be produced here, where the caller's
membership and active assignments are intersected with the curriculum data.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from apps.curriculum.models import (
    AssessmentObjective,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)
from apps.schools.models import Membership, TeacherAssignment

#: Roles allowed to see the whole school's curriculum rather than assigned rows.
LEADERSHIP_ROLES = frozenset(
    {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
)
#: Roles permitted to create or edit curriculum content (permission matrix, 6.2).
CONTENT_EDITOR_ROLES = frozenset({Membership.Role.COORDINATOR, Membership.Role.HEAD})


def _require_membership(membership):
    if not membership or membership.status != Membership.Status.ACTIVE:
        raise PermissionDenied("An active school membership is required.")
    return membership


def active_assignments(membership, on_date=None):
    """Return the caller's active TeacherAssignment rows for ``on_date``."""
    _require_membership(membership)
    on_date = on_date or timezone.localdate()
    return (
        TeacherAssignment.objects.for_school(membership.school_id)
        .filter(
            Q(effective_until__isnull=True) | Q(effective_until__gte=on_date),
            teacher_id=membership.user_id,
            is_active=True,
            effective_from__lte=on_date,
        )
        .select_related("subject", "school_class")
    )


def assert_can_edit_curriculum(membership):
    """Coordinators and Heads maintain curriculum content; nobody else may."""
    _require_membership(membership)
    if membership.role not in CONTENT_EDITOR_ROLES:
        raise PermissionDenied("Only a Coordinator or Head of Cambridge can edit curriculum data.")
    return membership


def visible_schemes(membership, on_date=None, selectable_only=True):
    """Schemes the caller may read.

    Leadership sees every scheme in the school. A teacher sees only schemes whose
    subject *and* class both match one of their active assignments.
    """
    _require_membership(membership)
    queryset = SchemeOfWork.objects.for_school(membership.school_id).select_related(
        "subject", "school_class", "academic_year", "term"
    )
    if selectable_only:
        queryset = queryset.selectable(on_date).filter(status=SchemeOfWork.Status.PUBLISHED)

    if membership.role in LEADERSHIP_ROLES:
        return queryset

    pairs = active_assignments(membership, on_date).values_list("subject_id", "school_class_id")
    if not pairs:
        return queryset.none()
    scope = Q()
    for subject_id, class_id in pairs:
        scope |= Q(subject_id=subject_id, school_class_id=class_id)
    return queryset.filter(scope)


def _assert_scheme_visible(membership, scheme_id, on_date=None):
    scheme = (
        visible_schemes(membership, on_date, selectable_only=False).filter(pk=scheme_id).first()
    )
    if scheme is None:
        # Do not leak whether the row exists outside the caller's scope.
        raise PermissionDenied("That scheme is not available for your assignments.")
    return scheme


def topic_options(membership, scheme_id, on_date=None):
    """Unit options for Lesson Plan LP-D01 level one."""
    _assert_scheme_visible(membership, scheme_id, on_date)
    return (
        Topic.objects.for_school(membership.school_id)
        .selectable(on_date)
        .filter(scheme_id=scheme_id)
    )


def subtopic_options(membership, topic_id, on_date=None):
    """Sub-unit options for Lesson Plan LP-D01 level two."""
    topic = (
        Topic.objects.for_school(membership.school_id)
        .filter(pk=topic_id)
        .select_related("scheme")
        .first()
    )
    if topic is None:
        raise PermissionDenied("That topic is not available for your assignments.")
    _assert_scheme_visible(membership, topic.scheme_id, on_date)
    return (
        Subtopic.objects.for_school(membership.school_id)
        .selectable(on_date)
        .filter(topic_id=topic_id)
    )


def learning_objective_options(membership, *, topic_id=None, subtopic_id=None, on_date=None):
    """LO options for Lesson Plan LP-D04 and Work Plan WP-D08.

    ``subtopic_id`` narrows to that sub-unit plus the topic-level objectives that
    are not tied to any sub-unit.
    """
    if not topic_id and not subtopic_id:
        raise ValueError("Provide topic_id or subtopic_id.")

    if subtopic_id:
        subtopic = (
            Subtopic.objects.for_school(membership.school_id)
            .filter(pk=subtopic_id)
            .select_related("topic")
            .first()
        )
        if subtopic is None:
            raise PermissionDenied("That sub-topic is not available for your assignments.")
        topic_id = subtopic.topic_id

    topic = Topic.objects.for_school(membership.school_id).filter(pk=topic_id).first()
    if topic is None:
        raise PermissionDenied("That topic is not available for your assignments.")
    _assert_scheme_visible(membership, topic.scheme_id, on_date)

    queryset = (
        LearningObjective.objects.for_school(membership.school_id)
        .selectable(on_date)
        .filter(topic_id=topic_id)
    )
    if subtopic_id:
        queryset = queryset.filter(Q(subtopic_id=subtopic_id) | Q(subtopic__isnull=True))
    return queryset


def assessment_objective_options(membership, subject_id, on_date=None):
    """AO options, intersected with the caller's assigned subjects."""
    _require_membership(membership)
    if membership.role not in LEADERSHIP_ROLES:
        allowed = set(active_assignments(membership, on_date).values_list("subject_id", flat=True))
        if subject_id not in allowed:
            raise PermissionDenied("That subject is not in your active assignments.")
    return (
        AssessmentObjective.objects.for_school(membership.school_id)
        .selectable(on_date)
        .filter(subject_id=subject_id)
    )


def resolve_selected_objectives(membership, objective_ids, *, topic_id=None, subtopic_id=None):
    """Validate submitted LO ids server-side.

    Section 12: "Server validates submitted option IDs; client labels are never
    trusted." Returns the objects in a stable order and raises when any id falls
    outside the authorised option set.
    """
    requested = [str(value) for value in objective_ids]
    if not requested:
        return []
    allowed = {
        str(item.pk): item
        for item in learning_objective_options(
            membership, topic_id=topic_id, subtopic_id=subtopic_id
        )
    }
    unknown = [value for value in requested if value not in allowed]
    if unknown:
        raise PermissionDenied("One or more selected objectives are not authorised.")
    return [allowed[value] for value in requested]
