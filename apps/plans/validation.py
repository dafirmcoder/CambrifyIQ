"""Draft and submission validation (plan sections 8.5 and 8.8).

Drafts may be incomplete. Submission requires every mandatory value, valid
dependencies, in-range attendance and cross-validated dates. Overflow is a
warning rather than an error: the approved box drives character guidance, and
the renderer applies the approved wrap policy.
"""

from django.core.exceptions import ValidationError

from apps.planning.models import FieldKind
from apps.plans.models import PlanState

#: Average characters per line for the deterministic 9pt body font, used to turn
#: an approved PDF box into character guidance (8.5).
CHARS_PER_INCH = 16.5
POINTS_PER_INCH = 72
LINE_HEIGHT_PT = 11.5


def capacity_for(field):
    """Estimate how many characters an approved box holds."""
    box = field.box
    if not box:
        return None
    x1, y1, x2, y2 = box
    lines = max(int((y2 - y1) // LINE_HEIGHT_PT), 1)
    chars_per_line = max(int((x2 - x1) / POINTS_PER_INCH * CHARS_PER_INCH), 1)
    return lines * chars_per_line


def text_guidance(template_version):
    """Character guidance per BLUE field, for live builder warnings."""
    guidance = {}
    for field in template_version.field_map():
        if field.kind != FieldKind.BLUE:
            continue
        guidance[field.field_id] = {
            "label": field.label,
            "required": field.is_required,
            "max_length": field.max_length,
            "capacity": capacity_for(field),
            "overflow_policy": field.overflow_policy or "wrap",
        }
    return guidance


def _roster_bounds(plan):
    school_class = plan.assignment.school_class
    return school_class.boys_count, school_class.girls_count


def validate_attendance(plan, *, boys, girls, allow_exception=False):
    """LP-D02/LP-D03 bounds. Counts cannot be negative or exceed the roster."""
    errors = {}
    boys_max, girls_max = _roster_bounds(plan)

    for label, value, maximum in (
        ("boys_present", boys, boys_max),
        ("girls_present", girls, girls_max),
    ):
        if value is None:
            continue
        if value < 0:
            errors[label] = "Attendance cannot be negative."
        elif maximum and value > maximum and not allow_exception:
            errors[label] = (
                f"{value} exceeds the roster count of {maximum}. "
                "Record an audited exception to continue."
            )
    if errors:
        raise ValidationError(errors)
    return True


def lesson_plan_issues(plan):
    """Blocking problems that prevent submitting a Lesson Plan."""
    issues = []
    if not plan.subtopic_id and not plan.topic_id:
        issues.append("LP-D01: select a unit or sub-unit.")
    if plan.boys_present is None:
        issues.append("LP-D02: record the boys attendance count.")
    if plan.girls_present is None:
        issues.append("LP-D03: record the girls attendance count.")
    if not plan.objective_labels:
        issues.append("LP-D04: select at least one learning objective.")
    if not (plan.main_teaching_activity or "").strip():
        issues.append("LP-T01: describe the main teaching activity.")
    if not (plan.assessment_ideas or "").strip():
        issues.append("LP-T02: describe the assessment ideas.")
    # LP-T03 is optional by the verified register.

    term = plan.work_plan_row.work_plan.term if plan.work_plan_row_id else None
    if term and not (term.starts_on <= plan.lesson_date <= term.ends_on):
        issues.append("LP-S03: the lesson date falls outside the term.")

    assignment = plan.assignment
    if assignment.effective_from > plan.lesson_date:
        issues.append("The lesson date precedes the teaching assignment.")
    if assignment.effective_until and assignment.effective_until < plan.lesson_date:
        issues.append("The teaching assignment had ended by the lesson date.")
    return issues


def work_plan_issues(plan):
    """Blocking problems that prevent submitting a Work Plan."""
    issues = []
    rows = list(plan.rows.all()) if plan.pk else []
    if not rows:
        issues.append("The plan has no calendar weeks. Generate the term weeks first.")
        return issues

    planned = [row for row in rows if row.objective_labels or row.event_label]
    if not planned:
        issues.append("WP-D08: plan at least one teaching week.")

    for row in rows:
        if row.is_special_week:
            continue
        if not row.objective_labels:
            issues.append(f"Week {row.week_number}: select a topic or learning objective.")
    return issues


def plan_issues(plan):
    from apps.plans.models import LessonPlan

    return lesson_plan_issues(plan) if isinstance(plan, LessonPlan) else work_plan_issues(plan)


def overflow_warnings(plan):
    """Non-blocking overflow warnings derived from the approved boxes."""
    from apps.plans.models import LessonPlan

    if not isinstance(plan, LessonPlan):
        return []

    guidance = text_guidance(plan.template_version)
    values = {
        "LP-T01": plan.main_teaching_activity,
        "LP-T02": plan.assessment_ideas,
        "LP-T03": plan.notes_remarks,
    }
    warnings = []
    for field_id, text in values.items():
        rule = guidance.get(field_id)
        if not rule or not text:
            continue
        capacity = rule["capacity"]
        if capacity and len(text) > capacity:
            warnings.append(
                f"{field_id} ({rule['label']}): about {len(text)} characters entered "
                f"for a box that fits roughly {capacity}. "
                f"The approved policy is '{rule['overflow_policy']}'."
            )
    return warnings


def assert_ready_for_submission(plan):
    """Raise unless the plan satisfies every mandatory rule."""
    if plan.state not in {PlanState.DRAFT, PlanState.RETURNED} and plan.state not in {
        PlanState.SUBMITTED,
        PlanState.UNDER_REVIEW,
        PlanState.RESUBMITTED,
    }:
        raise ValidationError("This plan can no longer be submitted.")
    if issues := plan_issues(plan):
        raise ValidationError(issues)
    return True
