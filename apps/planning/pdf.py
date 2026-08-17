"""Deterministic, dynamically paginated PDF output for Semester Work Plans."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _paragraph(value, style):
    return Paragraph((value or "—").replace("\n", "<br/>"), style)


def render_work_plan(plan, output):
    """Render all calendar rows, allowing ReportLab to create as many pages as needed."""

    document = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        leftMargin=0.35 * inch,
        rightMargin=0.35 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        title=f"Semester Work Plan — {plan.assignment.subject.name}",
    )
    styles = getSampleStyleSheet()
    title = styles["Title"]
    title.alignment = TA_CENTER
    heading = styles["Heading3"]
    heading.alignment = TA_CENTER
    cell = styles["BodyText"]
    cell.fontSize = 7.5
    cell.leading = 9
    header = styles["BodyText"]
    header.fontSize = 8
    header.leading = 9
    header.alignment = TA_CENTER
    header.textColor = colors.white

    story = [
        Paragraph("SEMESTER WORK PLAN", title),
        Paragraph(plan.school.name.upper(), heading),
        Paragraph(
            f"{plan.assignment.school_class.name.upper()} · {plan.academic_year.name} · "
            f"{plan.assignment.subject.name.upper()} · {plan.term.name.upper()}",
            heading,
        ),
        Spacer(1, 0.18 * inch),
    ]
    rows = [[
        _paragraph("MONTH", header),
        _paragraph("WEEK", header),
        _paragraph("TOPIC / LEARNING OBJECTIVES", header),
        _paragraph("REMARKS", header),
    ]]
    for week in plan.weeks.select_related("topic").prefetch_related("objective_selections").order_by("sequence"):
        objectives = "<br/>".join(
            f"{selection.code_snapshot}: {selection.text_snapshot}"
            for selection in week.objective_selections.all()
        )
        curriculum = week.event_label if not week.is_instructional else objectives
        if week.is_instructional and week.topic_id:
            curriculum = f"<b>{week.topic.title}</b><br/>{curriculum}" if curriculum else f"<b>{week.topic.title}</b>"
        rows.append(
            [
                _paragraph(week.month_label, cell),
                _paragraph(week.week_label, cell),
                _paragraph(curriculum, cell),
                _paragraph(week.remarks, cell),
            ]
        )
    table = Table(rows, colWidths=(1.0 * inch, 1.45 * inch, 4.1 * inch, 3.0 * inch), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D4B73")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#6B7280")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [table, Spacer(1, 0.18 * inch), Paragraph("RESOURCES", heading), _paragraph(plan.resources, cell)]
    document.build(story)
