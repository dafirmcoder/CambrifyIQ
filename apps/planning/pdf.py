"""Deterministic, dynamically paginated PDF output for Semester Work Plans."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.planning.services import calculate_work_plan_coverage


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
    rows = [
        [
            _paragraph("MONTH", header),
            _paragraph("WEEK", header),
            _paragraph("TOPIC / LEARNING OBJECTIVES", header),
            _paragraph("REMARKS", header),
        ]
    ]
    for week in (
        plan.weeks.select_related("topic", "subtopic")
        .prefetch_related(
            "objective_selections__objective__topic",
            "objective_selections__objective__subtopic",
        )
        .order_by("sequence")
    ):
        objectives_list = []
        for selection in week.objective_selections.all():
            obj_text = f"<b>{selection.code_snapshot}</b>: {selection.text_snapshot}"
            obj = getattr(selection, "objective", None)
            if obj and week.topic_id:
                is_cross = (obj.topic_id != week.topic_id) or (
                    week.subtopic_id and obj.subtopic_id != week.subtopic_id
                )
                if is_cross:
                    source_parts = []
                    if obj.topic:
                        source_parts.append(obj.topic.title)
                    if obj.subtopic:
                        source_parts.append(obj.subtopic.title)
                    if source_parts:
                        ctx = " · ".join(source_parts)
                        obj_text += (
                            f" <font color='#6B7280' size='6.5'><i>[Context: {ctx}]</i></font>"
                        )
            objectives_list.append(obj_text)
        objectives = "<br/>".join(objectives_list)

        curriculum = week.event_label if not week.is_instructional else objectives
        if week.is_instructional:
            header_parts = []
            if week.topic_id:
                header_parts.append(f"<b>Topic:</b> {week.topic.title}")
            if week.subtopic_id:
                header_parts.append(f"<b>Unit:</b> {week.subtopic.title}")
            if header_parts:
                curriculum_header = "<br/>".join(header_parts)
                curriculum = (
                    f"{curriculum_header}<br/>{curriculum}" if curriculum else curriculum_header
                )

        week_col = f"<b>{week.week_label}</b>"
        if week.is_instructional:
            week_col += (
                f"<br/><font color='#4B5563' size='7'>Lessons: {week.lessons_per_week}</font>"
            )

        rows.append(
            [
                _paragraph(week.month_label, cell),
                _paragraph(week_col, cell),
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
    coverage = calculate_work_plan_coverage(plan)
    coverage_text = (
        f"<b>Curriculum Coverage:</b> {coverage['projected_covered_objectives']} / "
        f"{coverage['total_objectives']} objectives ({coverage['projected_objective_percent']}%) "
        f"· Previous: {coverage['previously_covered_objectives']} ({coverage['previous_objective_percent']}%) "
        f"· Added: {coverage['current_plan_objectives']} "
        f"· Topics Covered: {coverage['covered_topics']} / {coverage['total_topics']} "
        f"({coverage['projected_topic_percent']}%)"
    )
    story += [
        table,
        Spacer(1, 0.12 * inch),
        Paragraph(coverage_text, cell),
        Spacer(1, 0.12 * inch),
        Paragraph("RESOURCES", heading),
        _paragraph(plan.resources, cell),
    ]
    document.build(story)


def render_lesson_plan(plan, output):
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"Lesson Plan - {plan.assignment.subject.name}",
    )
    styles = getSampleStyleSheet()
    title = styles["Title"]
    title.alignment = TA_CENTER
    normal = styles["BodyText"]
    normal.fontSize = 10
    normal.leading = 14

    story = [
        Paragraph("LESSON PLAN", title),
        Spacer(1, 0.2 * inch),
    ]

    meta_data = [
        [
            Paragraph(f"<b>SUBJECT:</b> {plan.assignment.subject.name}", normal),
            Paragraph(f"<b>DATE:</b> {plan.lesson_date.strftime('%d %B %Y')}", normal),
        ],
        [Paragraph(f"<b>UNIT/SUB-UNIT:</b> {plan.topic.title}", normal), ""],
        [Paragraph("<b>ATTENDANCE:</b>", normal), ""],
        [
            Paragraph(f"<b>BOYS:</b> {plan.boys_attendance}", normal),
            Paragraph(f"<b>GIRLS:</b> {plan.girls_attendance}", normal),
        ],
    ]
    meta_table = Table(meta_data, colWidths=(3.5 * inch, 3.5 * inch))
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>RESOURCES</b>", normal))
    all_resources = [
        "Lesson Notes",
        "Projector",
        "Laptop",
        "Learner's Book",
        "Teacher's Resource",
        "Whiteboard/marker",
    ]
    selected_resources = plan.resources or []
    for r in all_resources:
        mark = "[X]" if r in selected_resources else "[ ]"
        story.append(Paragraph(f"{mark} {r}", normal))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>LEARNING OBJECTIVES</b>", normal))
    story.append(Paragraph("By the end of this lesson, learners should be able to:", normal))
    for obj in plan.objective_selections.all():
        story.append(Paragraph(f"- {obj.code_snapshot}: {obj.text_snapshot}", normal))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>MAIN TEACHING ACTIVITY</b>", normal))
    story.append(Paragraph((plan.main_teaching_activity or "").replace("\n", "<br/>"), normal))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>ASSESSMENT IDEAS</b>", normal))
    story.append(Paragraph((plan.assessment_ideas or "").replace("\n", "<br/>"), normal))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("<b>NOTES/REMARKS</b>", normal))
    story.append(Paragraph((plan.notes_remarks or "").replace("\n", "<br/>"), normal))
    story.append(Spacer(1, 0.1 * inch))

    document.build(story)
