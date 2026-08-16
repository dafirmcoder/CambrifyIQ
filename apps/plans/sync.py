"""Idempotent offline synchronisation (plan sections 10.3 and 11).

The PWA queues operations while offline and replays them on reconnect. Three
guarantees matter:

* **Idempotent** — a replayed ``operation_id`` is recognised and not applied twice.
* **Conflict-aware** — an operation based on a stale revision is rejected as a
  conflict for explicit resolution, never silently merged (plan risk register).
* **Audited** — every outcome is recorded against the device and user.
"""

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.plans import services
from apps.plans.models import LessonPlan, SyncOperation, WorkPlan, WorkPlanRow

#: Operations the client may replay.
SUPPORTED_OPERATIONS = frozenset(
    {"lesson_plan.save", "work_plan.save_row", "work_plan.save_resources"}
)


def payload_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _record(membership, operation, *, result, plan=None, detail=None):
    return SyncOperation.all_objects.create(
        school_id=membership.school_id,
        user_id=membership.user_id,
        operation_id=operation.get("operation_id", ""),
        device_id=operation.get("device_id", ""),
        plan_type=operation.get("plan_type", ""),
        plan_id=plan.pk if plan is not None else operation.get("plan_id") or None,
        base_revision=operation.get("base_revision"),
        payload_hash=payload_hash(operation.get("payload", {})),
        result=result,
        detail=detail or {},
    )


def _load_plan(membership, model, plan_id):
    plan = model.objects.for_school(membership.school_id).filter(pk=plan_id).first()
    if plan is None:
        raise PermissionDenied("That plan is not available.")
    return plan


@transaction.atomic
def apply_operation(*, membership, operation):
    """Apply one queued operation and return its outcome."""
    operation_id = (operation.get("operation_id") or "").strip()
    if not operation_id:
        raise ValidationError("Every sync operation needs an operation_id.")

    existing = SyncOperation.all_objects.filter(
        user_id=membership.user_id, operation_id=operation_id
    ).first()
    if existing is not None:
        # Replay of an operation already seen: return the original outcome.
        return {
            "operation_id": operation_id,
            "result": SyncOperation.Result.DUPLICATE,
            "original_result": existing.result,
            "detail": existing.detail,
        }

    name = operation.get("name")
    if name not in SUPPORTED_OPERATIONS:
        record = _record(
            membership,
            operation,
            result=SyncOperation.Result.REJECTED,
            detail={"reason": f"Unsupported operation '{name}'."},
        )
        return {
            "operation_id": operation_id,
            "result": record.result,
            "detail": record.detail,
        }

    payload = operation.get("payload") or {}
    base_revision = operation.get("base_revision")

    try:
        if name == "lesson_plan.save":
            plan = _load_plan(membership, LessonPlan, operation.get("plan_id"))
            services.save_lesson_plan(
                membership=membership,
                plan=plan,
                base_revision=base_revision,
                subtopic_id=payload.get("subtopic_id", ...),
                objective_ids=payload.get("objective_ids"),
                boys_present=payload.get("boys_present", ...),
                girls_present=payload.get("girls_present", ...),
                main_teaching_activity=payload.get("main_teaching_activity"),
                assessment_ideas=payload.get("assessment_ideas"),
                notes_remarks=payload.get("notes_remarks"),
                attendance_exception=payload.get("attendance_exception"),
            )
        elif name == "work_plan.save_row":
            plan = _load_plan(membership, WorkPlan, operation.get("plan_id"))
            row = (
                WorkPlanRow.objects.for_school(membership.school_id)
                .filter(pk=payload.get("row_id"), work_plan=plan)
                .first()
            )
            if row is None:
                raise PermissionDenied("That work plan row is not available.")
            services.save_work_plan_row(
                membership=membership,
                plan=plan,
                row=row,
                base_revision=base_revision,
                objective_ids=payload.get("objective_ids"),
                remarks=payload.get("remarks"),
            )
        else:
            plan = _load_plan(membership, WorkPlan, operation.get("plan_id"))
            services.save_work_plan_resources(
                membership=membership,
                plan=plan,
                resources=payload.get("resources", ""),
                base_revision=base_revision,
            )
    except services.RevisionConflict as conflict:
        record = _record(
            membership,
            operation,
            result=SyncOperation.Result.CONFLICT,
            detail={"messages": conflict.messages},
        )
        return {
            "operation_id": operation_id,
            "result": record.result,
            "detail": record.detail,
        }
    except (ValidationError, PermissionDenied) as error:
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        record = _record(
            membership,
            operation,
            result=SyncOperation.Result.REJECTED,
            detail={"messages": messages},
        )
        return {
            "operation_id": operation_id,
            "result": record.result,
            "detail": record.detail,
        }

    try:
        _record(membership, operation, result=SyncOperation.Result.APPLIED, plan=plan)
    except IntegrityError:
        # A concurrent replay of the same ID won the race; treat as duplicate.
        return {"operation_id": operation_id, "result": SyncOperation.Result.DUPLICATE}

    return {
        "operation_id": operation_id,
        "result": SyncOperation.Result.APPLIED,
        "revision": plan.revision,
    }


def apply_batch(*, membership, operations):
    """Apply a queue in order, returning one outcome per operation."""
    results = []
    for operation in operations:
        try:
            results.append(apply_operation(membership=membership, operation=operation))
        except ValidationError as error:
            results.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "result": SyncOperation.Result.REJECTED,
                    "detail": {"messages": error.messages},
                }
            )
    return results
