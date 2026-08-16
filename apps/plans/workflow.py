"""Plan workflow engine (plan section 7.5).

One state machine serves both plan types:

    draft → submitted → under_review → approved → archived
                     ↘ returned → resubmitted ↗

Rules enforced here:

* Only permitted transitions run; anything else raises.
* Every transition writes an immutable ``PlanReview`` row and an ``AuditLog``
  entry recording actor, previous state, new state and comment.
* A return always requires a comment.
* Approved plans are immutable; editing one must create a new revision.
* Delegated approval is explicit and audited.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.plans.models import ALLOWED_TRANSITIONS, PlanReview, PlanState
from apps.schools.models import AuditLog, Membership

#: Roles that may review, return or recommend (permission matrix 6.2).
REVIEWER_ROLES = frozenset(
    {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
)
#: Roles that may grant final approval.
#: Section 21.2 leaves Head-only versus Director-final approval open; both are
#: currently permitted. Narrow this set once the owner decides.
APPROVER_ROLES = frozenset({Membership.Role.HEAD, Membership.Role.DIRECTOR})


def plan_type_of(plan):
    from apps.plans.models import LessonPlan

    return "lesson_plan" if isinstance(plan, LessonPlan) else "work_plan"


def _require_active(membership):
    if not membership or membership.status != Membership.Status.ACTIVE:
        raise PermissionDenied("An active school membership is required.")
    return membership


def assert_is_author(membership, plan):
    _require_active(membership)
    if plan.school_id != membership.school_id:
        raise PermissionDenied("That plan belongs to another school.")
    if plan.author_id != membership.user_id:
        raise PermissionDenied("Only the plan author can perform this action.")
    return plan


def assert_can_view(membership, plan):
    """Authors see their own plans; leadership sees the whole school."""
    _require_active(membership)
    if plan.school_id != membership.school_id:
        raise PermissionDenied("That plan belongs to another school.")
    if plan.author_id == membership.user_id:
        return plan
    if membership.role in REVIEWER_ROLES:
        return plan
    raise PermissionDenied("You do not have access to that plan.")


def assert_can_review(membership, plan):
    _require_active(membership)
    if plan.school_id != membership.school_id:
        raise PermissionDenied("That plan belongs to another school.")
    if membership.role not in REVIEWER_ROLES:
        raise PermissionDenied("Only a Coordinator, Head or Director can review plans.")
    if plan.author_id == membership.user_id:
        raise PermissionDenied("You cannot review your own plan.")
    return plan


@transaction.atomic
def _transition(*, membership, plan, action, new_state, comment="", audit_action=None):
    previous = plan.state
    if not plan.can_transition_to(new_state):
        raise ValidationError(
            f"A {plan.get_state_display()} plan cannot move to {new_state.replace('_', ' ')}."
        )

    plan.state = new_state
    update_fields = ["state", "updated_at"]

    if new_state in {PlanState.SUBMITTED, PlanState.RESUBMITTED}:
        plan.submitted_at = timezone.now()
        update_fields.append("submitted_at")
    if new_state == PlanState.APPROVED:
        plan.approved_at = timezone.now()
        plan.approved_by_id = membership.user_id
        update_fields += ["approved_at", "approved_by"]

    plan.save(update_fields=update_fields)

    plan_type = plan_type_of(plan)
    PlanReview.all_objects.create(
        school_id=plan.school_id,
        plan_type=plan_type,
        plan_id=plan.pk,
        actor_id=membership.user_id,
        action=action,
        previous_state=previous,
        new_state=new_state,
        comment=comment,
    )
    AuditLog.all_objects.create(
        school_id=plan.school_id,
        actor_id=membership.user_id,
        action=audit_action or f"{plan_type}.{action}",
        target_type=plan_type,
        target_id=str(plan.pk),
        metadata={
            "previous_state": previous,
            "new_state": new_state,
            "revision": plan.revision,
            "comment": comment,
        },
    )
    return plan


def submit(*, membership, plan, comment=""):
    """Author submits a draft, or resubmits a returned plan."""
    assert_is_author(membership, plan)
    from apps.plans.validation import assert_ready_for_submission

    assert_ready_for_submission(plan)

    if plan.state == PlanState.RETURNED:
        return _transition(
            membership=membership,
            plan=plan,
            action=PlanReview.Action.RESUBMITTED,
            new_state=PlanState.RESUBMITTED,
            comment=comment,
        )
    return _transition(
        membership=membership,
        plan=plan,
        action=PlanReview.Action.SUBMITTED,
        new_state=PlanState.SUBMITTED,
        comment=comment,
    )


def claim_review(*, membership, plan, comment=""):
    """A reviewer takes ownership of a submitted plan."""
    assert_can_review(membership, plan)
    return _transition(
        membership=membership,
        plan=plan,
        action=PlanReview.Action.CLAIMED,
        new_state=PlanState.UNDER_REVIEW,
        comment=comment,
    )


def return_for_changes(*, membership, plan, comment):
    """Return a plan to its author. A comment is mandatory (7.5, 13.4)."""
    assert_can_review(membership, plan)
    if not (comment or "").strip():
        raise ValidationError("A comment is required when returning a plan.")
    return _transition(
        membership=membership,
        plan=plan,
        action=PlanReview.Action.RETURNED,
        new_state=PlanState.RETURNED,
        comment=comment,
    )


def approve(*, membership, plan, comment=""):
    """Grant academic approval. The plan becomes immutable."""
    assert_can_review(membership, plan)
    if membership.role not in APPROVER_ROLES:
        raise PermissionDenied("Only a Head of Cambridge or Director can approve a plan.")
    from apps.plans.validation import assert_ready_for_submission

    assert_ready_for_submission(plan)
    return _transition(
        membership=membership,
        plan=plan,
        action=PlanReview.Action.APPROVED,
        new_state=PlanState.APPROVED,
        comment=comment,
    )


def archive(*, membership, plan, comment=""):
    """Move an approved plan into read-only history."""
    _require_active(membership)
    if membership.role not in REVIEWER_ROLES:
        raise PermissionDenied("Only leadership can archive a plan.")
    return _transition(
        membership=membership,
        plan=plan,
        action=PlanReview.Action.ARCHIVED,
        new_state=PlanState.ARCHIVED,
        comment=comment,
    )


def history(plan):
    """Full immutable transition history, newest first."""
    return PlanReview.all_objects.filter(
        school_id=plan.school_id, plan_type=plan_type_of(plan), plan_id=plan.pk
    ).select_related("actor")


def transitions_for(membership, plan):
    """Which actions the caller may take right now, for UI rendering."""
    actions = []
    is_author = plan.author_id == membership.user_id
    is_reviewer = membership.role in REVIEWER_ROLES and not is_author

    if is_author and plan.state in {PlanState.DRAFT, PlanState.RETURNED}:
        actions.append("submit")
    if is_reviewer:
        if PlanState.UNDER_REVIEW in ALLOWED_TRANSITIONS.get(plan.state, set()):
            actions.append("claim")
        if PlanState.RETURNED in ALLOWED_TRANSITIONS.get(plan.state, set()):
            actions.append("return")
        if membership.role in APPROVER_ROLES and PlanState.APPROVED in ALLOWED_TRANSITIONS.get(
            plan.state, set()
        ):
            actions.append("approve")
        if PlanState.ARCHIVED in ALLOWED_TRANSITIONS.get(plan.state, set()):
            actions.append("archive")
    return actions
