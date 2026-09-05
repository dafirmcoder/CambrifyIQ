"""Deterministic, dynamically paginated PDF output for Semester Work Plans matching Cambridge & school branding."""

import os
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.planning.services import calculate_work_plan_coverage


def _ordinal_day(day_num):
    """Format day integer with uppercase ordinal suffix, e.g. 24 -> 24TH, 1 -> 1ST."""
    if 11 <= (day_num % 100) <= 13:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(day_num % 10, "TH")
    return f"{day_num}{suffix}"


def _fit_image(img_path, max_w, max_h):
    """Load an image and return an Image Flowable with strictly preserved natural aspect ratio."""
    from PIL import Image as PILImage
    try:
        p = Path(img_path)
        if not p.exists():
            return None
        with PILImage.open(p) as im:
            w, h = im.size
        if w <= 0 or h <= 0:
            return None
        aspect = w / h
        if aspect >= (max_w / max_h):
            draw_w = max_w
            draw_h = max_w / aspect
        else:
            draw_h = max_h
            draw_w = max_h * aspect
        return Image(str(p), width=draw_w, height=draw_h)
    except Exception:
        return None


def _get_school_logo_flowable(school, max_w=160, max_h=48, target_w=None, target_h=None):
    """Return an Image flowable if this school has uploaded a logo, otherwise a tenant-specific text badge."""
    effective_w = target_w or max_w
    effective_h = target_h or max_h
    if school.logo_url:
        clean_path = school.logo_url.replace("/media/", "").lstrip("/")
        # Try media root
        media_path = Path(settings.MEDIA_ROOT) / clean_path
        img_flowable = _fit_image(media_path, effective_w, effective_h)
        if img_flowable:
            return img_flowable
        # Try base dir / static
        static_path = Path(settings.BASE_DIR) / clean_path
        img_flowable = _fit_image(static_path, effective_w, effective_h)
        if img_flowable:
            return img_flowable

    # Check if school name matches Leera and Leera logo is available
    if "leera" in school.name.lower() or "leera" in getattr(school, "slug", "").lower():
        leera_logo = Path("E:/LEERA/LOGOS/Leera International School.png")
        img_flowable = _fit_image(leera_logo, effective_w, effective_h)
        if img_flowable:
            return img_flowable

    # Dynamic fallback to the school's own name badge (no cross-tenant leakage)
    logo_style = ParagraphStyle(
        "SchoolLogoFallback",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#0f172a"),
    )
    return Paragraph(
        f"<b>{school.name.upper()}</b><br/><font color='#0B4F8A' size='7.5'><i>Cambridge International School</i></font>",
        logo_style,
    )


def _get_cambridge_logo_flowable(max_w=170, max_h=38, target_w=None, target_h=None):
    """Return the official Cambridge Assessment logo flowable with preserved natural aspect ratio."""
    effective_w = target_w or max_w
    effective_h = target_h or max_h
    for p in [
        Path(settings.BASE_DIR) / "static" / "img" / "cambridge_logo.png",
        Path("E:/LEERA/LOGOS/CIE.PNG"),
    ]:
        flowable = _fit_image(p, effective_w, effective_h)
        if flowable:
            return flowable

    # Text fallback if image missing
    cb_style = ParagraphStyle(
        "CambridgeLogoFallback",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=11.5,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#1e293b"),
    )
    return Paragraph(
        "<b>Cambridge Assessment</b><br/><font size='7.5' color='#475569'>International Education</font>",
        cb_style,
    )


def render_work_plan(plan, output):
    """Render all calendar rows matching the official LIS Semester Work Plan template."""
    from apps.core.tenant import tenant_scope

    with tenant_scope(plan.school):
        weeks = list(
            plan.weeks.select_related("topic", "subtopic", "calendar_week")
            .prefetch_related(
                "objective_selections__objective__topic",
                "objective_selections__objective__subtopic",
            )
            .order_by("sequence")
        )
        coverage = calculate_work_plan_coverage(plan)

    # Printable width: landscape letter (11 * 72 = 792pt) - 2 * 28.8pt (0.4in margins) = 734.4pt -> 734pt
    total_w = 734
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
        title=f"Semester Work Plan — {plan.subject_display}",
        author=plan.author.get_full_name() or plan.author.email,
    )# Base typography styles
    subhead_style = ParagraphStyle(
        "SubheadRow",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#000000"),
    )
    subhead_school_style = ParagraphStyle(
        "SubheadSchoolRow",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#000000"),
    )

    tbl_header_style = ParagraphStyle(
        "TblHeader",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#000000"),
    )

    month_cell_style = ParagraphStyle(
        "MonthCell",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#000000"),
    )

    week_cell_style = ParagraphStyle(
        "WeekCell",
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#000000"),
    )

    topic_cell_style = ParagraphStyle(
        "TopicCell",
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#1e293b"),
    )

    remarks_cell_style = ParagraphStyle(
        "RemarksCell",
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#334155"),
    )

    # ══════════════════════════════════════════════════════════════════════
    # 1. TOP HEADER ROW (3 Columns: School Logo | Title | Cambridge Logo)
    # ══════════════════════════════════════════════════════════════════════
    school_logo_flowable = _get_school_logo_flowable(plan.school, max_w=160, max_h=48)
    cambridge_logo_flowable = _get_cambridge_logo_flowable(max_w=170, max_h=38)

    title_flowable = Paragraph(
        "<b>SEMESTER WORK PLAN</b>",
        ParagraphStyle(
            "DocMainTitle",
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#000000"),
        ),
    )

    header_table = Table(
        [[school_logo_flowable, title_flowable, cambridge_logo_flowable]],
        colWidths=(160, 404, 170),
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    # ══════════════════════════════════════════════════════════════════════
    # 2. METADATA SUBHEADER BOX (Bordered, Stacked 4 Rows)
    # ══════════════════════════════════════════════════════════════════════
    class_label = plan.class_display
    term_start = plan.term.starts_on.strftime("%b").upper()
    term_end = plan.term.ends_on.strftime("%b").upper()

    meta_rows = [
        [Paragraph(f"<b>{plan.school.name.upper()}</b>", subhead_school_style)],
        [Paragraph(f"<b>{class_label.upper()} LONG TERM PLAN {plan.academic_year.name}</b>", subhead_style)],
        [Paragraph(f"<b>{plan.subject_display.upper()}</b>", subhead_style)],
        [Paragraph(f"<b>{plan.term.name.upper()} ({term_start} – {term_end})</b>", subhead_style)],
    ]

    meta_table = Table(meta_rows, colWidths=(total_w,))
    meta_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#64748b")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    # ══════════════════════════════════════════════════════════════════════
    # 3. MAIN WORK PLAN TABLE (MONTHS | WEEK | TOPIC/ LO | REMARKS)
    # ══════════════════════════════════════════════════════════════════════
    table_rows = [
        [
            Paragraph("<b>MONTHS</b>", tbl_header_style),
            Paragraph("<b>WEEK</b>", tbl_header_style),
            Paragraph("<b>TOPIC/ LEARNING OBJECTIVE</b>", tbl_header_style),
            Paragraph("<b>REMARKS</b>", tbl_header_style),
        ]
    ]

    table_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00A859")),  # Solid Brand Green Header
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#64748b")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 1), (-1, -1), 6),
        ("RIGHTPADDING", (0, 1), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]

    # Month grouping tracker for row spans
    month_groups = []
    current_month = None
    current_start = 1

    for idx, week in enumerate(weeks, start=1):
        m_name = (week.month_label or "TERM").upper()
        if m_name != current_month:
            if current_month is not None:
                month_groups.append((current_month, current_start, idx - 1))
            current_month = m_name
            current_start = idx

        # Format Week cell: Week number bold + Date range with ordinal days (e.g. 24TH – 28TH)
        week_date_str = ""
        if week.calendar_week:
            start_d = week.calendar_week.starts_on
            end_d = week.calendar_week.ends_on
            week_date_str = f"{_ordinal_day(start_d.day)} – {_ordinal_day(end_d.day)}"
        elif " " in week.week_label:
            parts = week.week_label.split(":", 1)
            if len(parts) > 1:
                week_date_str = parts[1].strip()

        week_html_parts = [f"<font size='9'><b>{week.sequence}</b></font>"]
        if week_date_str:
            week_html_parts.append(f"<font size='7' color='#000000'><b>{week_date_str}</b></font>")
        week_html = "<br/>".join(week_html_parts)

        # Format Topic / Learning Objective cell
        if not week.is_instructional:
            event_name = week.event_label or "SPECIAL EVENT"
            curriculum_html = f"<br/><font size='8.5' color='#0B4F8A'><b>{event_name.upper()}</b></font><br/>"
        else:
            curriculum_parts = []

            # Main Topic / Unit Title: Bold, Navy Blue (#0B4F8A / #003366), Uppercase
            topic_heading = ""
            if week.topic_id and week.topic:
                topic_heading = f"TOPIC: {week.topic.title.upper()}"
                if week.subtopic_id and week.subtopic:
                    topic_heading = f"{week.subtopic.title.upper()}"
            elif week.subtopic_id and week.subtopic:
                topic_heading = f"UNIT: {week.subtopic.title.upper()}"

            if topic_heading:
                curriculum_parts.append(f"<font color='#0B4F8A' size='8'><b>{topic_heading}</b></font>")

            # Learning Objectives Section
            selections = list(week.objective_selections.all())
            if selections:
                curriculum_parts.append(
                    "<font color='#0056B3' size='7.5'><b>Learning Objectives (Cambridge Scheme of Work):</b></font>"
                )
                lo_lines = []
                for sel in selections:
                    bullet_tag = "<font color='#00A859'><b>[✓]</b></font>" if sel.is_met else "•"
                    line = f"&nbsp;&nbsp;{bullet_tag} <b>{sel.code_snapshot}</b>: {sel.text_snapshot}"
                    obj = getattr(sel, "objective", None)
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
                                line += f" <font color='#64748b' size='6.5'><i>[{ctx}]</i></font>"
                    lo_lines.append(line)
                curriculum_parts.append("<br/>".join(lo_lines))

            # Weekly Lesson Structure (if instructional)
            if week.lessons_per_week > 0:
                curriculum_parts.append(
                    f"<font color='#008000' size='7.5'><b>Weekly Lesson Structure (Timetable: {week.lessons_per_week} Lessons/Week):</b></font>"
                )
                lesson_notes = getattr(week, "lesson_structure_notes", None) or getattr(week, "weekly_reflection", "")
                if lesson_notes:
                    note_lines = [
                        f"&nbsp;&nbsp;• {line.strip()}"
                        for line in lesson_notes.split("\n")
                        if line.strip()
                    ]
                    if note_lines:
                        curriculum_parts.append("<br/>".join(note_lines))

            curriculum_html = "<br/>".join(curriculum_parts) if curriculum_parts else "—"

        remarks_html = week.remarks.strip().replace("\n", "<br/>") if week.remarks else "—"

        table_rows.append(
            [
                Paragraph(m_name, month_cell_style),
                Paragraph(week_html, week_cell_style),
                Paragraph(curriculum_html, topic_cell_style),
                Paragraph(remarks_html, remarks_cell_style),
            ]
        )

    if current_month is not None:
        month_groups.append((current_month, current_start, len(weeks)))

    # Apply Span and vertical alignment for Month and Week columns
    for _m_name, start_r, end_r in month_groups:
        if start_r < end_r:
            table_styles.append(("SPAN", (0, start_r), (0, end_r)))
        table_styles.append(("VALIGN", (0, start_r), (0, end_r), "MIDDLE"))
        table_styles.append(("ALIGN", (0, start_r), (0, end_r), "CENTER"))
        table_styles.append(("BACKGROUND", (0, start_r), (0, end_r), colors.HexColor("#ffffff")))

    # Align and valign for week cells
    for r_idx in range(1, len(table_rows)):
        table_styles.append(("VALIGN", (1, r_idx), (1, r_idx), "MIDDLE"))
        table_styles.append(("ALIGN", (1, r_idx), (1, r_idx), "CENTER"))
        table_styles.append(("VALIGN", (3, r_idx), (3, r_idx), "MIDDLE"))

    # Exact column widths summing to 734pt
    col_widths = (72, 72, 510, 80)
    work_plan_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    work_plan_table.setStyle(TableStyle(table_styles))

    # Resources & Coverage Summary Footer
    resources_text = plan.resources.strip() if plan.resources else "—"
    all_selections = [sel for w in weeks for sel in w.objective_selections.all()]
    met_count = len(set(sel.objective_id for sel in all_selections if sel.is_met))
    planned_count = coverage["current_plan_objectives"]
    met_pct = round((met_count / planned_count) * 100, 1) if planned_count > 0 else 0.0

    coverage_text = (
        f"<b>Curriculum Coverage:</b> {coverage['projected_covered_objectives']} / "
        f"{coverage['total_objectives']} objectives ({coverage['projected_objective_percent']}%) "
        f"· Current Plan: {coverage['current_plan_objectives']} "
        f"· Met / Achieved: {met_count}/{planned_count} ({met_pct}%) "
        f"· Topics Covered: {coverage['covered_topics']} / {coverage['total_topics']} "
        f"({coverage['projected_topic_percent']}%)"
    )

    story = [
        header_table,
        meta_table,
        Spacer(1, 0.08 * inch),
        work_plan_table,
        Spacer(1, 0.1 * inch),
        Paragraph(f"<b>RESOURCES:</b> {resources_text.replace(chr(10), '<br/>')}", topic_cell_style),
        Spacer(1, 0.05 * inch),
        Paragraph(coverage_text, topic_cell_style),
    ]

    document.build(story)


def render_lesson_plan(plan, output):
    """Render a Lesson Plan PDF matching the official LEERA Lesson Plan template with dynamic school branding on all pages."""
    from PIL import Image as PILImage

    # Page size and dimensions (Portrait Letter: 612 x 792 pt)
    page_w, page_h = letter
    left_m = 36
    right_m = 36
    top_m = 76     # Clear height reserved for header & red/indigo accent bar
    bottom_m = 32  # Clear height reserved for bottom green footer bar
    usable_w = page_w - left_m - right_m  # 540 pt

    school = plan.school
    school_name = school.name.upper()
    academic_yr = getattr(plan, "academic_year", None) or (getattr(plan, "term", None).academic_year if getattr(plan, "term", None) else None)
    current_year = academic_yr.name if academic_yr else "2026"

    # Resolve School Logo image path for header canvas drawing
    logo_img_path = None
    if school.logo_url:
        clean_path = school.logo_url.replace("/media/", "").lstrip("/")
        candidate = Path(settings.MEDIA_ROOT) / clean_path
        if candidate.exists():
            logo_img_path = candidate
        else:
            candidate_static = Path(settings.BASE_DIR) / clean_path
            if candidate_static.exists():
                logo_img_path = candidate_static
    if not logo_img_path and ("leera" in school.name.lower() or "leera" in getattr(school, "slug", "").lower()):
        leera_candidate = Path("E:/LEERA/LOGOS/Leera International School.png")
        if leera_candidate.exists():
            logo_img_path = leera_candidate

    def _draw_page_header_and_footer(canvas, doc):
        canvas.saveState()

        # ── TOP HEADER (Rendered on EVERY Page) ──
        # 1. School Logo (Left)
        if logo_img_path:
            try:
                with PILImage.open(logo_img_path) as im:
                    orig_w, orig_h = im.size
                if orig_w > 0 and orig_h > 0:
                    aspect = orig_w / orig_h
                    max_w, max_h = 150, 44
                    if aspect >= (max_w / max_h):
                        draw_w = max_w
                        draw_h = max_w / aspect
                    else:
                        draw_h = max_h
                        draw_w = max_h * aspect
                    logo_x = left_m
                    logo_y = page_h - 18 - draw_h
                    canvas.drawImage(str(logo_img_path), logo_x, logo_y, width=draw_w, height=draw_h, mask="auto")
            except Exception:
                pass
        else:
            # Fallback text badge for school on left
            canvas.setFont("Helvetica-Bold", 10)
            canvas.setFillColor(colors.HexColor("#0f172a"))
            canvas.drawString(left_m, page_h - 36, school_name)
            canvas.setFont("Helvetica-Oblique", 7.5)
            canvas.setFillColor(colors.HexColor("#0B4F8A"))
            canvas.drawString(left_m, page_h - 46, "Cambridge International School")

        # 2. Header Text (Right)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.setFillColor(colors.HexColor("#C8102E"))  # Crimson Red school name
        canvas.drawRightString(page_w - right_m, page_h - 34, school_name)

        canvas.setFont("Helvetica-Bold", 13.5)
        canvas.setFillColor(colors.HexColor("#1E1B4B"))  # Deep Indigo LESSON PLAN title
        canvas.drawRightString(page_w - right_m, page_h - 50, "LESSON PLAN")

        # 3. Two-Tone Accent Divider Bar
        divider_y = page_h - 60
        split_x = left_m + 230

        # Red bar (left)
        canvas.setStrokeColor(colors.HexColor("#D3122A"))
        canvas.setLineWidth(3.5)
        canvas.line(left_m, divider_y, split_x, divider_y)

        # Indigo bar (right)
        canvas.setStrokeColor(colors.HexColor("#2E1065"))
        canvas.setLineWidth(2)
        canvas.line(split_x, divider_y, page_w - right_m, divider_y)

        # ── BOTTOM FOOTER (Rendered on EVERY Page) ──
        footer_h = 20
        canvas.setFillColor(colors.HexColor("#70B020"))  # Solid Brand Green Footer Bar
        canvas.rect(0, 0, page_w, footer_h, fill=1, stroke=0)

        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawCentredString(page_w / 2.0, 6, f"© {school_name} - {current_year}")

        canvas.restoreState()

    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title=f"Lesson Plan — {plan.assignment.subject.name}",
    )
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "LPBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1e293b"),
    )
    section_head_style = ParagraphStyle(
        "LPSectionHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#2E1065"),  # Deep purple/indigo
        spaceBefore=7,
        spaceAfter=2,
    )

    box_table_style = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF2CC")),  # Warm light yellow/cream
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]

    box_grid_style = box_table_style + [
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]

    story = []

    # 1. Metadata Grid (2 Columns: Left Column 270pt, Right Column 270pt)
    unit_title = plan.subtopic.title if plan.subtopic else (plan.topic.title if plan.topic else "—")
    if plan.subtopic and plan.topic:
        unit_title = f"{plan.topic.title} · {plan.subtopic.title}"

    teacher_name = plan.author.get_full_name() or plan.author.username
    class_name = plan.assignment.school_class.name
    subject_name = plan.assignment.subject.name
    date_str = f"{plan.lesson_date:%d %B %Y}"
    if plan.originating_work_plan_week:
        date_str += f" (Week {plan.originating_work_plan_week.sequence})"

    meta_rows = [
        [
            Paragraph(f"<b>TEACHER:</b> {teacher_name}", body_style),
            Paragraph(f"<b>CLASS:</b> {class_name}", body_style),
        ],
        [
            Paragraph(f"<b>SUBJECT:</b> {subject_name}", body_style),
            Paragraph(f"<b>DATE:</b> {date_str}", body_style),
        ],
        [
            Paragraph(f"<b>UNIT/SUB-UNIT:</b> {unit_title}", body_style),
            Paragraph(
                f"<b>ATTENDANCE:</b> &nbsp;&nbsp;&nbsp;&nbsp;<b>BOYS:</b> {plan.boys_attendance or 0} &nbsp;&nbsp;&nbsp;&nbsp;<b>GIRLS:</b> {plan.girls_attendance or 0}",
                body_style,
            ),
        ],
    ]

    meta_table = Table(meta_rows, colWidths=(270, 270))
    meta_table.setStyle(TableStyle(box_grid_style))
    story.append(meta_table)

    # 2. Resources Section
    story.append(Paragraph("<b>RESOURCES</b>", section_head_style))
    selected_resources = plan.resources or []
    res_col1_items = ["Lesson Notes", "Projector", "Laptop"]
    res_col2_items = ["Learner's Book", "Teacher's Resource", "Whiteboard/marker"]

    def _render_res_col(items):
        lines = []
        for it in items:
            checked = it in selected_resources
            prefix = "<font color='#15803d'><b>[✓]</b></font>" if checked else "<font color='#64748b'>•</font>"
            lines.append(f"{prefix} {it}")
        return Paragraph("<br/>".join(lines), body_style)

    res_table = Table(
        [[_render_res_col(res_col1_items), _render_res_col(res_col2_items)]],
        colWidths=(270, 270),
    )
    res_table.setStyle(TableStyle(box_table_style))
    story.append(res_table)

    # 3. Learning Objectives Section
    story.append(Paragraph("<b>LEARNING OBJECTIVES</b>", section_head_style))
    obj_lines = ["By the end of this lesson, learners should be able to:"]
    for sel in plan.objective_selections.select_related("objective"):
        obj_code = sel.code_snapshot or (sel.objective.code if sel.objective else "")
        obj_text = sel.text_snapshot or (sel.objective.text if sel.objective else "")
        obj_lines.append(f"• <b>{obj_code}</b>: {obj_text}")

    if len(obj_lines) == 1:
        obj_lines.append("• <i>No specific objectives selected.</i>")

    obj_table = Table([[Paragraph("<br/>".join(obj_lines), body_style)]], colWidths=(usable_w,))
    obj_table.setStyle(TableStyle(box_table_style))
    story.append(obj_table)

    # 4. Main Teaching Activity Section
    story.append(Paragraph("<b>MAIN TEACHING ACTIVITY</b>", section_head_style))
    main_act_text = (plan.main_teaching_activity or "").strip()
    if not main_act_text:
        main_act_text = "<br/><br/><br/>"
    else:
        main_act_text = main_act_text.replace("\n", "<br/>")

    act_table = Table([[Paragraph(main_act_text, body_style)]], colWidths=(usable_w,))
    act_table.setStyle(TableStyle(box_table_style))
    story.append(act_table)

    # 5. Assessment Ideas Section
    story.append(Paragraph("<b>ASSESSMENT IDEAS</b>", section_head_style))
    assess_text = (plan.assessment_ideas or "").strip()
    if not assess_text:
        assess_text = "<br/><br/><br/>"
    else:
        assess_text = assess_text.replace("\n", "<br/>")

    assess_table = Table([[Paragraph(assess_text, body_style)]], colWidths=(usable_w,))
    assess_table.setStyle(TableStyle(box_table_style))
    story.append(assess_table)

    # 6. Notes / Remarks Section
    story.append(Paragraph("<b>NOTES/REMARKS</b>", section_head_style))
    notes_text = (plan.notes_remarks or "").strip()
    if not notes_text:
        notes_text = "<br/><br/>"
    else:
        notes_text = notes_text.replace("\n", "<br/>")

    notes_table = Table([[Paragraph(notes_text, body_style)]], colWidths=(usable_w,))
    notes_table.setStyle(TableStyle(box_table_style))
    story.append(notes_table)

    # Build document with header/footer rendered on every single page
    document.build(
        story,
        onFirstPage=_draw_page_header_and_footer,
        onLaterPages=_draw_page_header_and_footer,
    )
