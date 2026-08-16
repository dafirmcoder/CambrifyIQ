"""Verified template annotation register (CAMS plan 8.2, 8.3 and Appendix A).

These declarations are the single source of truth used to seed
``TemplateField`` rows for TemplateVersion 1 of each plan type.

Lesson Plan values are *visually verified* from TEMPLATE.pdf: four RED controlled
pickers, three BLUE free-text inputs and five fixed/system entries. Coordinates
are measured from the flattened raster in PDF points with a top-left origin and
are implementation baselines that must be refined against the approved clean
master before Template Acceptance.

Work Plan values are a *functional proposal*. The received sample carries no
colour annotations, so section 8.3's decision gate must be closed by the
template owner before the Work Plan version is locked.
"""

from apps.planning.models import ControlType, FieldKind, PlanType

# Source inspection record (Appendix C).
LESSON_PLAN_SOURCE = {
    "filename": "TEMPLATE.pdf",
    "sha256": "77e37bed5ed70ed5530984754ce2e3243d9081dd4028915a413f5f3a54f65004",
    "pages": 1,
    "width_pt": 595.32,
    "height_pt": 841.92,
    "content_form": "Single flattened raster image; no usable text or drawing objects.",
}

WORK_PLAN_SOURCE = {
    "filename": "LIS - SEMESTER 1 WORKPLAN TEMPLATE - 26'27.pdf",
    "sha256": "f7c5ae287190ff155961db28bcbb6907273f3968cd90ac7b3627c98ae470b344",
    "pages": 3,
    "width_pt": 792.0,
    "height_pt": 612.0,
    "content_form": "Mixed text, vector table drawings and image assets.",
}


def _field(field_id, label, kind, control, **kwargs):
    return {
        "field_id": field_id,
        "label": label,
        "kind": kind,
        "control": control,
        **kwargs,
    }


#: Lesson Plan register — 4 RED, 3 BLUE, 5 system entries.
LESSON_PLAN_FIELDS = [
    _field(
        "LP-D01",
        "UNIT/SUB-UNIT",
        FieldKind.RED,
        ControlType.CASCADING_SELECT,
        page=1,
        sequence=10,
        is_required=True,
        option_source="curriculum.subtopic",
        help_text="Scheme, then unit, then sub-unit, limited to your active assignment.",
        box=(138.4, 155.5, 263.4, 190.7),
        source_note="Red annotation around UNIT/SUB-UNIT value area.",
        overflow_policy="clip",
    ),
    _field(
        "LP-D02",
        "ATTENDANCE: BOYS",
        FieldKind.RED,
        ControlType.INTEGER_PICKER,
        page=1,
        sequence=20,
        is_required=True,
        option_source="roster.boys",
        min_value=0,
        help_text="0 to the active boys count for the selected class and date.",
        box=(406.4, 160.1, 493.9, 176.9),
        source_note="Boys circle overlaps visually with Girls circle.",
    ),
    _field(
        "LP-D03",
        "ATTENDANCE: GIRLS",
        FieldKind.RED,
        ControlType.INTEGER_PICKER,
        page=1,
        sequence=30,
        is_required=True,
        option_source="roster.girls",
        min_value=0,
        help_text="0 to the active girls count for the selected class and date.",
        box=(406.4, 175.4, 496.7, 192.2),
        source_note="Separate control despite touching annotation.",
    ),
    _field(
        "LP-D04",
        "LEARNING OBJECTIVES",
        FieldKind.RED,
        ControlType.MULTI_SELECT,
        page=1,
        sequence=40,
        is_required=True,
        option_source="curriculum.learning_objective",
        help_text="Objectives linked to the selected unit or sub-unit.",
        box=(66.2, 280.8, 528.6, 378.7),
        source_note="Large Learning Objectives box.",
        overflow_policy="wrap",
    ),
    _field(
        "LP-T01",
        "MAIN TEACHING ACTIVITY",
        FieldKind.BLUE,
        ControlType.TEXTAREA,
        page=1,
        sequence=50,
        is_required=True,
        help_text="Paragraphs, bullets and line breaks are retained.",
        box=(66.2, 407.7, 530.0, 501.0),
        source_note="Main Teaching Activity dotted box.",
        overflow_policy="warn",
    ),
    _field(
        "LP-T02",
        "ASSESSMENT IDEAS",
        FieldKind.BLUE,
        ControlType.TEXTAREA,
        page=1,
        sequence=60,
        is_required=True,
        help_text="Assessment-for-learning or evidence description.",
        box=(66.2, 523.9, 530.0, 617.1),
        source_note="Assessment Ideas dotted box.",
        overflow_policy="warn",
    ),
    _field(
        "LP-T03",
        "NOTES/REMARKS",
        FieldKind.BLUE,
        ControlType.TEXTAREA,
        page=1,
        sequence=70,
        is_required=False,
        help_text="Teacher notes, reflection or follow-up. May be left blank.",
        box=(66.2, 640.0, 530.0, 705.8),
        source_note="Notes/Remarks dotted box.",
        overflow_policy="warn",
    ),
    _field(
        "LP-S01",
        "Branding, title, borders and labels",
        FieldKind.SYSTEM,
        ControlType.STATIC,
        page=1,
        sequence=80,
        is_readonly=True,
        help_text="Fixed in the clean master, including per-school branding.",
    ),
    _field(
        "LP-S02",
        "SUBJECT",
        FieldKind.SYSTEM,
        ControlType.TEXT,
        page=1,
        sequence=90,
        is_readonly=True,
        option_source="assignment.subject",
        help_text="Read-only from the assignment or originating Work Plan row.",
    ),
    _field(
        "LP-S03",
        "DATE",
        FieldKind.SYSTEM,
        ControlType.DATE,
        page=1,
        sequence=100,
        is_readonly=True,
        option_source="calendar.lesson_date",
        help_text="Derived from lesson context and validated against term dates.",
    ),
    _field(
        "LP-S04",
        "ATTENDANCE total",
        FieldKind.SYSTEM,
        ControlType.COMPUTED,
        page=1,
        sequence=110,
        is_readonly=True,
        option_source="computed.attendance_total",
        help_text="Computed as LP-D02 + LP-D03.",
    ),
    _field(
        "LP-S05",
        "RESOURCES list",
        FieldKind.SYSTEM,
        ControlType.STATIC,
        page=1,
        sequence=120,
        is_readonly=True,
        help_text=(
            "Fixed prompts: Lesson Notes, Projector, Laptop, Learner's Book, "
            "Teacher's Resource, Whiteboard/marker."
        ),
    ),
]

#: Fixed resource prompts printed on the Lesson Plan master (LP-S05).
LESSON_PLAN_RESOURCE_PROMPTS = [
    "Lesson Notes",
    "Projector",
    "Laptop",
    "Learner's Book",
    "Teacher's Resource",
    "Whiteboard/marker",
]

#: Work Plan register — proposed classification pending the 8.3 decision gate.
WORK_PLAN_FIELDS = [
    _field(
        "WP-D01",
        "School",
        FieldKind.SYSTEM,
        ControlType.TEXT,
        page=1,
        sequence=10,
        is_readonly=True,
        option_source="school.current",
        help_text="Current school and branding.",
    ),
    _field(
        "WP-D02",
        "Class or year group",
        FieldKind.RED,
        ControlType.SELECT,
        page=1,
        sequence=20,
        is_required=True,
        option_source="assignment.school_class",
        help_text="Active classes in your assignments.",
    ),
    _field(
        "WP-D03",
        "Academic year",
        FieldKind.RED,
        ControlType.SELECT,
        page=1,
        sequence=30,
        is_required=True,
        option_source="calendar.academic_year",
    ),
    _field(
        "WP-D04",
        "Subject",
        FieldKind.RED,
        ControlType.SELECT,
        page=1,
        sequence=40,
        is_required=True,
        option_source="assignment.subject",
        help_text="Active subject and class assignments only.",
    ),
    _field(
        "WP-D05",
        "Semester",
        FieldKind.RED,
        ControlType.SELECT,
        page=1,
        sequence=50,
        is_required=True,
        option_source="calendar.term",
    ),
    _field(
        "WP-D06",
        "Month",
        FieldKind.SYSTEM,
        ControlType.COMPUTED,
        page=1,
        sequence=60,
        is_readonly=True,
        option_source="calendar.month",
        help_text="Derived from the selected calendar and row date range.",
    ),
    _field(
        "WP-D07",
        "Week number and date range",
        FieldKind.SYSTEM,
        ControlType.COMPUTED,
        page=1,
        sequence=70,
        is_readonly=True,
        option_source="calendar.week",
        help_text="Generated from term and week records; history keeps its label.",
    ),
    _field(
        "WP-D08",
        "TOPIC/LEARNING OBJECTIVE",
        FieldKind.RED,
        ControlType.CASCADING_SELECT,
        page=1,
        sequence=80,
        is_required=True,
        option_source="curriculum.learning_objective",
        help_text="Scheme, then topic or sub-topic, then objective.",
        overflow_policy="wrap",
    ),
    _field(
        "WP-T01",
        "REMARKS",
        FieldKind.BLUE,
        ControlType.TEXTAREA,
        page=1,
        sequence=90,
        is_required=False,
        help_text="Progress, exceptions or follow-up for the week.",
        overflow_policy="warn",
    ),
    _field(
        "WP-T02",
        "RESOURCES",
        FieldKind.BLUE,
        ControlType.TEXTAREA,
        page=3,
        sequence=100,
        is_required=False,
        help_text="Owner to confirm free text versus controlled resource selector.",
        overflow_policy="warn",
    ),
    _field(
        "WP-S01",
        "Revision, assessment and end-of-semester events",
        FieldKind.SYSTEM,
        ControlType.STATIC,
        page=3,
        sequence=110,
        is_readonly=True,
        help_text="Preserve approved event labels for weeks 15 to 17.",
    ),
]

#: Fixed calendar events preserved from the approved sample (7.2, Appendix A).
WORK_PLAN_SPECIAL_EVENTS = {
    15: "Revision Week",
    16: "Semester Assessments",
    17: "End of First Semester & PTC",
}

#: Page structure of the received three-page sample (8.3).
WORK_PLAN_PAGE_LAYOUT = {
    1: {"months": ["August", "September"], "weeks": range(1, 6)},
    2: {"months": ["October", "November"], "weeks": range(6, 13)},
    3: {"months": ["November", "December"], "weeks": range(13, 18)},
}

REGISTERS = {
    PlanType.LESSON_PLAN: LESSON_PLAN_FIELDS,
    PlanType.WORK_PLAN: WORK_PLAN_FIELDS,
}

SOURCES = {
    PlanType.LESSON_PLAN: LESSON_PLAN_SOURCE,
    PlanType.WORK_PLAN: WORK_PLAN_SOURCE,
}


def register_for(plan_type):
    """Return the declared field register for a plan type."""
    return REGISTERS[plan_type]


def counts_by_kind(plan_type):
    """Field totals per annotation colour, used by acceptance tests (8.8)."""
    totals = {FieldKind.RED: 0, FieldKind.BLUE: 0, FieldKind.SYSTEM: 0}
    for field in register_for(plan_type):
        totals[field["kind"]] += 1
    return totals
