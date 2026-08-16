"""Deterministic PDF rendering (plan section 8.6).

Requirements honoured here:

* Page size and orientation match the approved sources — Lesson Plan A4 portrait
  (595.32 x 841.92 pt), Work Plan US Letter landscape (792 x 612 pt) over three
  pages covering weeks 1 to 17.
* Field IDs resolve to human-readable labels, using the snapshot stored on the
  plan so historical labels survive later curriculum edits.
* Text is placed inside the approved boxes with deterministic wrapping and the
  approved clip or continuation behaviour.
* Plan version, template version, status, timestamp and a verification code
  appear in the metadata footer.
* **No annotation circles ever appear**: output is drawn from the clean master
  definition, never from the annotated raster source.

Rendering refuses to run unless the template version is renderable, i.e. a clean
unmarked master has been approved (section 2 production-master constraint).
"""

import hashlib
import io
from textwrap import wrap

from django.core.exceptions import ValidationError
from django.utils import timezone
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from apps.planning.models import PlanType
from apps.plans.models import GeneratedDocument
from apps.plans.workflow import plan_type_of

INK = HexColor("#102a43")
SOFT = HexColor("#486581")
LINE = HexColor("#b9c7d6")
ACCENT = HexColor("#1565c0")

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
BODY_SIZE = 9
LINE_HEIGHT = 11.5

WORK_PLAN_PAGE_SIZE = (792, 612)
LESSON_PLAN_PAGE_SIZE = (595.32, 841.92)

#: Which weeks appear on each Work Plan page, from the approved sample (8.3).
WORK_PLAN_PAGES = ((1, 5), (6, 12), (13, 17))


def verification_code(plan, revision):
    """Short, stable code printed in the metadata area for authenticity checks."""
    seed = f"{plan.pk}:{revision}".encode()
    return hashlib.sha256(seed).hexdigest()[:10].upper()


def _new_canvas(buffer, page_size, plan):
    """Create a canvas that renders deterministically.

    Section 8.6 requires that the same saved version produces materially
    identical output, so the document ID and timestamps are derived from the
    plan and its revision rather than from the wall clock.
    """
    pdf = canvas.Canvas(buffer, pagesize=page_size, invariant=True)
    seed = hashlib.md5(f"{plan.pk}:{plan.revision}".encode()).hexdigest()
    pdf.setKeywords(f"revision:{plan.revision}")
    pdf._doc._ID = f"<{seed}><{seed}>".encode()
    return pdf


def _require_renderable(template_version):
    if not template_version.is_renderable:
        raise ValidationError(
            "This template version has no approved clean master, so it cannot "
            "produce production output. The annotated source contains the "
            "review circles and must never be released."
        )


def _wrap_lines(text, width_pt, font_size=BODY_SIZE):
    """Deterministic wrapping for a fixed-width font metric."""
    if not text:
        return []
    approx_char_width = font_size * 0.5
    chars = max(int(width_pt / approx_char_width), 8)
    lines = []
    for paragraph in str(text).splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(wrap(paragraph, chars) or [""])
    return lines


def _draw_boxed_text(pdf, box, text, *, overflow="wrap", font=BODY_FONT, size=BODY_SIZE):
    """Render text inside an approved box, clipping per the approved policy.

    Returns any lines that did not fit, so a continuation page can be produced
    once that policy is approved.
    """
    x1, y1, x2, y2 = box
    height = y2 - y1
    max_lines = max(int(height // LINE_HEIGHT), 1)
    lines = _wrap_lines(text, x2 - x1, size)
    visible, overflowed = lines[:max_lines], lines[max_lines:]

    pdf.setFont(font, size)
    pdf.setFillColor(INK)
    # Text flows from the top of the box downwards.
    cursor = y2 - size
    for line in visible:
        pdf.drawString(x1 + 2, cursor, line)
        cursor -= LINE_HEIGHT

    if overflowed and overflow == "clip":
        pdf.setFont(font, size - 1)
        pdf.setFillColor(SOFT)
        pdf.drawRightString(x2, y2 - 1, "…")
    return overflowed


def _flip(page_height, box):
    """Convert a top-left origin register box to ReportLab's bottom-left space."""
    x1, y1, x2, y2 = box
    return (x1, page_height - y2, x2, page_height - y1)


def _metadata_footer(pdf, plan, page_size, *, page_label, code):
    width, _ = page_size
    pdf.setFont(BODY_FONT, 6.5)
    pdf.setFillColor(SOFT)
    # Derived from the plan's own last-saved time so re-rendering an unchanged
    # revision reproduces identical bytes (8.6).
    stamp = timezone.localtime(plan.updated_at).strftime("%d %b %Y %H:%M")
    left = (
        f"Plan revision {plan.revision} · Template v{plan.template_version.version} · "
        f"Status {plan.get_state_display()}"
    )
    pdf.drawString(34, 20, left)
    pdf.drawCentredString(width / 2, 20, page_label)
    pdf.drawRightString(width - 34, 20, f"Generated {stamp} · Verify {code}")


def _header(pdf, plan, page_size, title):
    width, height = page_size
    school = plan.school
    pdf.setFillColor(ACCENT)
    pdf.rect(0, height - 6, width, 6, stroke=0, fill=1)
    pdf.setFont(BOLD_FONT, 13)
    pdf.setFillColor(INK)
    pdf.drawString(34, height - 34, school.name)
    pdf.setFont(BOLD_FONT, 10)
    pdf.setFillColor(ACCENT)
    pdf.drawRightString(width - 34, height - 34, title)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(0.7)
    pdf.line(34, height - 42, width - 34, height - 42)


def _label_value(pdf, x, y, label, value, *, value_width=170):
    pdf.setFont(BOLD_FONT, 7)
    pdf.setFillColor(SOFT)
    pdf.drawString(x, y, label.upper())
    pdf.setFont(BODY_FONT, 9)
    pdf.setFillColor(INK)
    lines = _wrap_lines(value or "—", value_width)
    pdf.drawString(x, y - 12, lines[0] if lines else "—")


def render_lesson_plan(plan):
    """One-page A4 portrait Lesson Plan drawn from the verified field map."""
    _require_renderable(plan.template_version)
    page_size = LESSON_PLAN_PAGE_SIZE
    width, height = page_size
    buffer = io.BytesIO()
    pdf = _new_canvas(buffer, page_size, plan)
    pdf.setTitle(f"Lesson Plan · {plan.assignment.subject.name}")

    _header(pdf, plan, page_size, "LESSON PLAN")

    # LP-S02 Subject and LP-S03 Date are read-only context values.
    top = height - 70
    _label_value(pdf, 34, top, "Subject", plan.assignment.subject.name)
    _label_value(pdf, 220, top, "Class", plan.assignment.school_class.name)
    _label_value(pdf, 360, top, "Date", plan.lesson_date.strftime("%d %B %Y"))
    _label_value(pdf, 470, top, "Teacher", plan.author.get_short_name())

    # LP-D01 unit / sub-unit.
    unit = " · ".join(
        part
        for part in (
            plan.topic.title if plan.topic_id else None,
            plan.subtopic.title if plan.subtopic_id else None,
        )
        if part
    )
    _label_value(pdf, 34, top - 38, "Unit / Sub-unit", unit or "—", value_width=300)

    # LP-D02 / LP-D03 / LP-S04 attendance block.
    box_y = top - 52
    pdf.setStrokeColor(LINE)
    pdf.rect(390, box_y - 6, width - 34 - 390, 34, stroke=1, fill=0)
    pdf.setFont(BOLD_FONT, 7)
    pdf.setFillColor(SOFT)
    pdf.drawString(398, box_y + 18, "ATTENDANCE")
    pdf.setFont(BODY_FONT, 8.5)
    pdf.setFillColor(INK)
    total = plan.attendance_total
    boys = plan.boys_present if plan.boys_present is not None else "—"
    girls = plan.girls_present if plan.girls_present is not None else "—"
    pdf.drawString(398, box_y + 4, f"Boys {boys}")
    pdf.drawString(452, box_y + 4, f"Girls {girls}")
    pdf.setFont(BOLD_FONT, 8.5)
    pdf.drawString(510, box_y + 4, f"Total {total if total is not None else '—'}")

    # LP-D04 objectives, then the three BLUE sections.
    objectives = "\n".join(
        f"{index}. {label}" for index, label in enumerate(plan.objective_labels, start=1)
    )
    sections = [
        ("LEARNING OBJECTIVES", objectives or "—", 96),
        ("MAIN TEACHING ACTIVITY", plan.main_teaching_activity, 132),
        ("ASSESSMENT IDEAS", plan.assessment_ideas, 108),
        ("NOTES / REMARKS", plan.notes_remarks, 84),
    ]
    cursor = box_y - 26
    for label, value, block_height in sections:
        pdf.setFont(BOLD_FONT, 7.5)
        pdf.setFillColor(ACCENT)
        pdf.drawString(34, cursor, label)
        cursor -= 6
        pdf.setStrokeColor(LINE)
        pdf.rect(34, cursor - block_height, width - 68, block_height, stroke=1, fill=0)
        _draw_boxed_text(
            pdf,
            (36, cursor - block_height + 4, width - 38, cursor - 4),
            value,
            overflow="clip",
        )
        cursor -= block_height + 18

    # LP-S05 fixed resource prompts.
    from apps.planning.register import LESSON_PLAN_RESOURCE_PROMPTS

    pdf.setFont(BOLD_FONT, 7.5)
    pdf.setFillColor(ACCENT)
    pdf.drawString(34, cursor, "RESOURCES")
    pdf.setFont(BODY_FONT, 8)
    pdf.setFillColor(INK)
    # Draw checkbox squares as vector rects: the core PDF fonts have no ballot glyph.
    box_x = 34
    for item in LESSON_PLAN_RESOURCE_PROMPTS:
        pdf.setStrokeColor(LINE)
        pdf.setLineWidth(0.6)
        pdf.rect(box_x, cursor - 16, 7, 7, stroke=1, fill=0)
        pdf.setFillColor(INK)
        pdf.drawString(box_x + 11, cursor - 15, item)
        box_x += 15 + pdf.stringWidth(item, BODY_FONT, 8)

    code = verification_code(plan, plan.revision)
    _metadata_footer(pdf, plan, page_size, page_label="Page 1 of 1", code=code)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue(), code


def render_work_plan(plan):
    """Three-page US Letter landscape Work Plan covering weeks 1 to 17."""
    _require_renderable(plan.template_version)
    page_size = WORK_PLAN_PAGE_SIZE
    width, height = page_size
    buffer = io.BytesIO()
    pdf = _new_canvas(buffer, page_size, plan)
    pdf.setTitle(f"Semester Work Plan · {plan.assignment.subject.name}")

    rows = {row.week_number: row for row in plan.rows.all()}
    code = verification_code(plan, plan.revision)
    total_pages = len(WORK_PLAN_PAGES)

    columns = (
        ("MONTH", 34, 74),
        ("WEEK", 108, 96),
        ("TOPIC / LEARNING OBJECTIVE", 204, 372),
        ("REMARKS", 576, width - 34 - 576),
    )

    for page_index, (first_week, last_week) in enumerate(WORK_PLAN_PAGES, start=1):
        _header(pdf, plan, page_size, "SEMESTER WORK PLAN")

        top = height - 66
        _label_value(pdf, 34, top, "Class", plan.assignment.school_class.name, value_width=120)
        _label_value(pdf, 170, top, "Subject", plan.assignment.subject.name, value_width=120)
        _label_value(pdf, 320, top, "Academic year", plan.academic_year.name, value_width=120)
        _label_value(pdf, 470, top, "Semester", plan.term.name, value_width=150)

        header_y = top - 34
        pdf.setFillColor(HexColor("#e9f3ff"))
        pdf.rect(34, header_y - 4, width - 68, 18, stroke=0, fill=1)
        pdf.setFont(BOLD_FONT, 7.5)
        pdf.setFillColor(INK)
        for title, x, _ in columns:
            pdf.drawString(x + 3, header_y + 3, title)

        cursor = header_y - 4
        pdf.setStrokeColor(LINE)
        pdf.setLineWidth(0.6)

        for number in range(first_week, last_week + 1):
            row = rows.get(number)
            objectives = "\n".join(row.objective_labels) if row else ""
            if row and row.event_label:
                objectives = (
                    row.event_label if not objectives else f"{row.event_label}\n{objectives}"
                )
            remarks = row.remarks if row else ""

            body_lines = max(
                len(_wrap_lines(objectives, columns[2][2] - 6)),
                len(_wrap_lines(remarks, columns[3][2] - 6)),
                1,
            )
            row_height = max(body_lines * LINE_HEIGHT + 8, 26)
            row_top = cursor
            row_bottom = cursor - row_height

            if row and row.event_label:
                pdf.setFillColor(HexColor("#fdf6e6"))
                pdf.rect(34, row_bottom, width - 68, row_height, stroke=0, fill=1)

            pdf.setStrokeColor(LINE)
            pdf.rect(34, row_bottom, width - 68, row_height, stroke=1, fill=0)
            for _, x, _w in columns[1:]:
                pdf.line(x, row_bottom, x, row_top)

            pdf.setFont(BODY_FONT, 8)
            pdf.setFillColor(INK)
            pdf.drawString(37, row_top - 13, row.month_label if row else "")
            pdf.setFont(BOLD_FONT, 8)
            pdf.drawString(111, row_top - 13, f"Week {number}")
            pdf.setFont(BODY_FONT, 6.5)
            pdf.setFillColor(SOFT)
            pdf.drawString(111, row_top - 22, row.week_label if row else "")

            topic_box = (
                columns[2][1] + 3,
                row_bottom + 4,
                columns[2][1] + columns[2][2] - 3,
                row_top - 4,
            )
            remarks_box = (
                columns[3][1] + 3,
                row_bottom + 4,
                columns[3][1] + columns[3][2] - 3,
                row_top - 4,
            )
            _draw_boxed_text(pdf, topic_box, objectives)
            _draw_boxed_text(pdf, remarks_box, remarks)
            cursor = row_bottom

        # WP-T02 resources, page three only, per the approved sample.
        if page_index == total_pages:
            pdf.setFont(BOLD_FONT, 7.5)
            pdf.setFillColor(ACCENT)
            pdf.drawString(34, cursor - 20, "RESOURCES")
            pdf.setStrokeColor(LINE)
            pdf.rect(34, cursor - 76, width - 68, 52, stroke=1, fill=0)
            _draw_boxed_text(pdf, (37, cursor - 72, width - 38, cursor - 28), plan.resources)

        _metadata_footer(
            pdf,
            plan,
            page_size,
            page_label=f"Page {page_index} of {total_pages}",
            code=code,
        )
        pdf.showPage()

    pdf.save()
    return buffer.getvalue(), code


def render(plan):
    """Render whichever plan type was supplied."""
    from apps.plans.models import LessonPlan

    return render_lesson_plan(plan) if isinstance(plan, LessonPlan) else render_work_plan(plan)


def generate_document(plan, *, record=True):
    """Render a plan and store its checksum record (11, GeneratedDocument)."""
    content, code = render(plan)
    checksum = hashlib.sha256(content).hexdigest()
    plan_type = plan_type_of(plan)
    label = "lesson-plan" if plan_type == PlanType.LESSON_PLAN else "work-plan"
    file_name = f"{label}-{plan.pk}-r{plan.revision}.pdf"

    document = None
    if record:
        document = GeneratedDocument.all_objects.create(
            school_id=plan.school_id,
            plan_type=plan_type,
            plan_id=plan.pk,
            plan_revision=plan.revision,
            template_version=plan.template_version,
            plan_state=plan.state,
            file_name=file_name,
            checksum=checksum,
            byte_size=len(content),
            verification_code=code,
        )
    return content, file_name, document
