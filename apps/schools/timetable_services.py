"""Timetable PDF parsing and automated teacher assignment provisioning services.

Comprehensive parsing engine supporting:
- aSc Timetables PDF exports (e.g. Leera International School exports with mirrored day names, double-period spans, multi-group room/lab annotations)
- Right-hand side subject list & count panel extraction (canonical teacher subject vocabulary)
- Multi-token cell disambiguation (Top line = Primary Subject, Middle = Class, Bottom = Room/Group)
- Global Cambridge Subject matching against canonical Curriculum Schemes of Work with class-level resolution (Primary, Lower Secondary, IGCSE, AS/A Level)
- Atomic provisioning of Subject, SchoolClass, TeacherAssignment, and TeacherScheduleSlot records
"""

import io
import re
import difflib
from collections import defaultdict
from datetime import time
import pypdf
import pypdfium2 as pdfium
import pdfplumber
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied

from apps.curriculum.models import SchemeOfWork, CurriculumFramework
from apps.schools.models import (
    School,
    Subject,
    SchoolClass,
    TeacherAssignment,
    TeacherTimetable,
    TeacherScheduleSlot,
    AuditLog,
)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYS_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
DAYS_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Base Global Cambridge Subjects dictionary
KNOWN_CAMBRIDGE_SUBJECTS = {
    "COMP-SC-PRC": ("Computer Science", "9618"),
    "COMP.SC": ("Computer Science", "9618"),
    "COMP SC": ("Computer Science", "9618"),
    "COMPUTER SCIENCE": ("Computer Science", "0478"),
    "COMPUTING": ("Computing", "0860"),
    "COMP": ("Computing", "0860"),
    "CMP": ("Computing", "0860"),
    "ICT": ("Information and Communication Technology", "0417"),
    "I.C.T.": ("Information and Communication Technology", "0417"),
    "IT": ("Information and Communication Technology", "0417"),
    "DIGLIT": ("Digital Literacy", "0082"),
    "DL": ("Digital Literacy", "0082"),
    "D.L.": ("Digital Literacy", "0082"),
    "DIGITAL LITERACY": ("Digital Literacy", "0082"),
    "MATH": ("Mathematics", "0862"),
    "MATHS": ("Mathematics", "0862"),
    "MTH": ("Mathematics", "0862"),
    "MAT": ("Mathematics", "0862"),
    "MATHEMATICS": ("Mathematics", "0862"),
    "ENG": ("English", "0861"),
    "ENGL": ("English", "0861"),
    "ENGLISH": ("English", "0861"),
    "FLE": ("English - First Language", "0500"),
    "ESL": ("English as a Second Language", "0876"),
    "ENGLISH AS A SECOND LANGUAGE": ("English as a Second Language", "0876"),
    "LIT": ("Literature in English", "0475"),
    "SCI": ("Science", "0893"),
    "SC": ("Science", "0893"),
    "SCIENCE": ("Science", "0893"),
    "BIO": ("Biology", "0610"),
    "BIOL": ("Biology", "0610"),
    "BIOLOGY": ("Biology", "0610"),
    "CHEM": ("Chemistry", "0620"),
    "CHM": ("Chemistry", "0620"),
    "CHEMISTRY": ("Chemistry", "0620"),
    "PHY": ("Physics", "0625"),
    "PHYS": ("Physics", "0625"),
    "PHYSICS": ("Physics", "0625"),
    "BUS": ("Business Studies", "0450"),
    "BST": ("Business Studies", "0450"),
    "BUS ST": ("Business Studies", "0450"),
    "BUSINESS": ("Business Studies", "0450"),
    "BUSINESS STUDIES": ("Business Studies", "0450"),
    "ECO": ("Economics", "0455"),
    "ECON": ("Economics", "0455"),
    "ECONOMICS": ("Economics", "0455"),
    "ACC": ("Accounting", "0452"),
    "ACCT": ("Accounting", "0452"),
    "ACCOUNTING": ("Accounting", "0452"),
    "GEO": ("Geography", "0460"),
    "GEOG": ("Geography", "0460"),
    "GEOGRAPHY": ("Geography", "0460"),
    "HIST": ("History", "0470"),
    "HIS": ("History", "0470"),
    "HISTORY": ("History", "0470"),
    "ART": ("Art & Design", "0073"),
    "ART & DESIGN": ("Art & Design", "0073"),
    "ART  DESIGN": ("Art & Design", "0073"),
    "MUSIC": ("Music", "0078"),
    "PE": ("Physical Education", "0081"),
    "P.E.": ("Physical Education", "0081"),
    "PHYSICAL EDUCATION": ("Physical Education", "0081"),
    "GP": ("Global Perspectives", "1129"),
    "GLOBAL PERSPECTIVES": ("Global Perspectives", "1129"),
    "FRE": ("French", "0520"),
    "FREN": ("French", "0520"),
    "FRENCH": ("French", "0520"),
    "SPA": ("Spanish", "0530"),
    "SPAN": ("Spanish", "0530"),
    "SPANISH": ("Spanish", "0530"),
    "GER": ("German", "0525"),
    "GERM": ("German", "0525"),
    "GERMAN": ("German", "0525"),
    "SWA": ("Swahili", "0262"),
    "KISW": ("Swahili", "0262"),
    "SWAHILI": ("Swahili", "0262"),
    "SOC": ("Humanities", "0839"),
    "SST": ("Humanities", "0839"),
    "HUMANITIES": ("Humanities", "0839"),
    "WELLBEING": ("Wellbeing", "0859"),
}

NON_LESSON_KEYWORDS = {
    "BREAKFAST", "BREAK", "TEA BREAK", "SNACK", "LUNCH", "RECESS", "ASSEMBLY",
    "DUTY", "HOMEROOM", "FREE", "PREPARATION", "PLANNING", "MEETING",
    "STAFF MEETING", "REGISTRATION", "PASTORAL", "LIBRARY", "SPORTS",
    "GAMES", "DEVOTION", "CHAPEL", "CLUBS", "CO-CURRICULAR", "TUTORIAL", "MORNING"
}

TIME_RANGE_REGEX = re.compile(
    r'(?P<start_h>\d{1,2})[:.](?P<start_m>\d{2})\s*(?P<start_p>am|pm)?\s*(?:-|–|to)\s*(?P<end_h>\d{1,2})[:.](?P<end_m>\d{2})\s*(?P<end_p>am|pm)?',
    re.IGNORECASE
)

PERIOD_REGEX = re.compile(r'(?:Period|Lesson|P)\s*(\d{1,2})', re.IGNORECASE)


def parse_time_str(hour_str, minute_str, period_str=None):
    h = int(hour_str)
    m = int(minute_str)
    if period_str:
        p = period_str.lower()
        if p == "pm" and h < 12:
            h += 12
        elif p == "am" and h == 12:
            h = 0
    elif h < 7:
        h += 12
    return f"{h:02d}:{m:02d}"


def unmirror(text):
    """Detect and un-mirror reversed day names from aSc PDF exports (e.g. 'yadnoM' -> 'Monday')."""
    if not text:
        return None
    joined = re.sub(r"\s+", "", text).lower()
    for candidate in (joined, joined[::-1]):
        match = difflib.get_close_matches(candidate, DAYS, n=1, cutoff=0.7)
        if match:
            return match[0].capitalize()
    return None


def clean_and_stitch_lines(lines):
    """Stitch trailing detached characters onto previous line tokens (e.g. 'Year 9-Atlanti' + 'c' -> 'Year 9-Atlantic')."""
    clean = [l.strip() for l in lines if l.strip()]
    stitched = []
    for l in clean:
        if stitched and len(l) <= 2 and l.isalpha() and not stitched[-1].endswith(" "):
            stitched[-1] = stitched[-1] + l
        else:
            stitched.append(l)
    return stitched


def infer_level_cambridge_code(subject_name, class_names_or_single):
    """Infer the precise Cambridge code by matching subject name and target class level (Primary, Lower Sec, IGCSE, AS/A Level)."""
    if isinstance(class_names_or_single, list):
        full_cls = " ".join(class_names_or_single).upper()
    else:
        full_cls = str(class_names_or_single).upper()

    s_name = subject_name.lower().strip()

    # 1. Cambridge International AS & A Level (Years 12-13 / AS / A2)
    if re.search(r'\b(AS-YR|A2|A-LEVEL|AS-LEVEL|YR 12|YR 13|YEAR 12|YEAR 13|GRADE 12|GRADE 13|STAGE 12|STAGE 13)\b', full_cls):
        if "computer science" in s_name:
            return "9618", "Cambridge International AS & A Level"
        if "information" in s_name or "it" in s_name or "ict" in s_name:
            return "9626", "Cambridge International AS & A Level"
        if "math" in s_name:
            return "9709", "Cambridge International AS & A Level"
        if "physics" in s_name:
            return "9702", "Cambridge International AS & A Level"
        if "chemistry" in s_name:
            return "9701", "Cambridge International AS & A Level"
        if "biology" in s_name:
            return "9700", "Cambridge International AS & A Level"
        if "business" in s_name:
            return "9609", "Cambridge International AS & A Level"
        if "econ" in s_name:
            return "9708", "Cambridge International AS & A Level"
        if "account" in s_name:
            return "9706", "Cambridge International AS & A Level"
        if "english" in s_name:
            return "9093", "Cambridge International AS & A Level"
        if "literature" in s_name:
            return "9695", "Cambridge International AS & A Level"
        if "geo" in s_name:
            return "9696", "Cambridge International AS & A Level"
        if "hist" in s_name:
            return "9489", "Cambridge International AS & A Level"
        if "global" in s_name:
            return "9239", "Cambridge International AS & A Level"

    # 2. Cambridge IGCSE (Years 10-11 / Grades 9-10 / Form 3-4 / Year 9)
    if re.search(r'\b(IGCSE|YEAR 10|YEAR 11|YR 10|YR 11|GRADE 10|GRADE 11|STAGE 10|STAGE 11|FORM 3|FORM 4|YEAR 9|YR 9|GRADE 9)\b', full_cls):
        if "computer science" in s_name:
            return "0478", "Cambridge IGCSE"
        if "information" in s_name or "ict" in s_name:
            return "0417", "Cambridge IGCSE"
        if "math" in s_name:
            return "0580", "Cambridge IGCSE"
        if "physics" in s_name:
            return "0625", "Cambridge IGCSE"
        if "chemistry" in s_name:
            return "0620", "Cambridge IGCSE"
        if "biology" in s_name:
            return "0610", "Cambridge IGCSE"
        if "business" in s_name:
            return "0450", "Cambridge IGCSE"
        if "econ" in s_name:
            return "0455", "Cambridge IGCSE"
        if "account" in s_name:
            return "0452", "Cambridge IGCSE"
        if "english" in s_name:
            return "0500", "Cambridge IGCSE"
        if "esl" in s_name or "second language" in s_name:
            return "0511", "Cambridge IGCSE"
        if "geo" in s_name:
            return "0460", "Cambridge IGCSE"
        if "hist" in s_name:
            return "0470", "Cambridge IGCSE"
        if "global" in s_name:
            return "0457", "Cambridge IGCSE"

    # 3. Cambridge Lower Secondary (Stages 7-9 / Years 7-9)
    if re.search(r'\b(YEAR 7|YEAR 8|YEAR 9|YR 7|YR 8|YR 9|GRADE 7|GRADE 8|GRADE 9|STAGE 7|STAGE 8|STAGE 9|FORM 1|FORM 2)\b', full_cls):
        if "computing" in s_name:
            return "0860", "Cambridge Lower Secondary"
        if "digital literacy" in s_name or "dl" in s_name:
            return "0082", "Cambridge Lower Secondary"
        if "math" in s_name:
            return "0862", "Cambridge Lower Secondary"
        if "science" in s_name:
            return "0893", "Cambridge Lower Secondary"
        if "english" in s_name:
            return "0861", "Cambridge Lower Secondary"
        if "esl" in s_name:
            return "0876", "Cambridge Lower Secondary"
        if "global" in s_name:
            return "1129", "Cambridge Lower Secondary"
        if "humanities" in s_name:
            return "0839", "Cambridge Lower Secondary"

    # 4. Cambridge Primary (Stages 1-6 / Years 1-6)
    if re.search(r'\b(YEAR [1-6]|YR [1-6]|GRADE [1-6]|STAGE [1-6]|CLASS [1-6])\b', full_cls):
        if "computing" in s_name:
            return "0059", "Cambridge Primary"
        if "digital literacy" in s_name or "dl" in s_name:
            return "0072", "Cambridge Primary"
        if "math" in s_name:
            return "0058", "Cambridge Primary"
        if "science" in s_name:
            return "0846", "Cambridge Primary"
        if "english" in s_name:
            return "0058", "Cambridge Primary"
        if "global" in s_name:
            return "0838", "Cambridge Primary"
        if "humanities" in s_name:
            return "0065", "Cambridge Primary"

    # Fallback default from SchemeOfWork
    matching_scheme = SchemeOfWork.objects.filter(subject_name__icontains=subject_name, is_active=True).first()
    if matching_scheme:
        return matching_scheme.subject_code, matching_scheme.framework.name

    return "", "Cambridge Curriculum"


def cell_words_text(bbox, words, pad=2.0):
    """Return reconstructed cell text from pdfplumber words, fixing trailing character splits."""
    x0, top, x1, bottom = bbox
    inside = [
        w for w in words
        if x0 - pad <= (w["x0"] + w["x1"]) / 2 < x1 + pad
        and top - pad <= (w["top"] + w["bottom"]) / 2 < bottom + pad
    ]
    inside.sort(key=lambda w: (round(w["top"] / 4), w["x0"]))
    toks = [w["text"] for w in inside]
    fixed = []
    for t in toks:
        if fixed and len(t) == 1 and t.islower() and fixed[-1][-1:].isalpha():
            fixed[-1] += t
        else:
            fixed.append(t)
    return " ".join(fixed).strip()


def decode_cell_entry(text_or_lines, global_cambridge_subjects=None):
    """
    Decodes an aSc or grid table cell into a single primary Global Cambridge Subject, Class, and Room/Group.
    Rules:
    1. Line 1 (or top token) in an aSc cell is the primary Subject code.
    2. If multiple subject codes exist in the cell (e.g. COMP.SC at top and COMP at bottom),
       that means it is ONE subject, and the bottom code is the Room or Group.
    3. Middle line is the Class name (e.g. AS-YR 12, YEAR 9-Pacific, 9-Atlantic, Grade 7A).
    """
    if isinstance(text_or_lines, str):
        raw_lines = [l.strip() for l in text_or_lines.splitlines() if l.strip()]
    else:
        raw_lines = [l.strip() for l in text_or_lines if l.strip()]

    lines = clean_and_stitch_lines(raw_lines)
    if not lines:
        return None

    full_text = " ".join(lines)
    upper_tokens = [t.upper().strip("(),.") for t in full_text.split()]
    for kw in NON_LESSON_KEYWORDS:
        if kw in upper_tokens or kw == full_text.upper():
            return None

    subject_raw = None
    subject_name = None
    cambridge_code = None
    class_name = None
    room_or_group = ""

    # Priority 1: Check the first line for the primary subject code
    first_line = lines[0].strip()
    for code, (s_name, c_code) in sorted(KNOWN_CAMBRIDGE_SUBJECTS.items(), key=lambda x: -len(x[0])):
        if first_line.upper() == code or first_line.upper().startswith(code + " ") or first_line.upper().startswith(code + "-"):
            subject_raw = code
            subject_name = s_name
            cambridge_code = c_code
            break

    # Priority 2: Check global Cambridge subjects dictionary across all lines
    if not subject_raw:
        for code, (s_name, c_code) in sorted(KNOWN_CAMBRIDGE_SUBJECTS.items(), key=lambda x: -len(x[0])):
            for line in lines:
                if re.search(r'\b' + re.escape(code) + r'\b', line, re.IGNORECASE):
                    subject_raw = code
                    subject_name = s_name
                    cambridge_code = c_code
                    break
            if subject_raw:
                break

    # Extract Class and Room/Group from remaining lines
    class_candidates = []
    extra_tokens = []

    for idx, line in enumerate(lines):
        line_clean = line.strip()
        # Skip if this line was purely the primary subject code
        if subject_raw and line_clean.upper() == subject_raw.upper():
            if idx > 0:
                room_or_group = line_clean
            continue

        line_no_time = re.sub(r'\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}', '', line_clean).strip()
        if not line_no_time:
            continue

        if re.search(r'\b(?:AS-YR|YEAR|YR|GRADE|STAGE|FORM|CLASS|G|Y)\b|\b\d{1,2}-[A-Za-z0-9]+\b|\b\d{1,2}[A-Za-z]?\b', line_no_time, re.IGNORECASE):
            cls_text = line_no_time
            if subject_raw:
                cls_text = re.sub(re.escape(subject_raw), '', cls_text, flags=re.IGNORECASE).strip()
            if cls_text:
                class_candidates.append(cls_text)
        else:
            extra_tokens.append(line_no_time)

    class_name = " ".join(class_candidates).strip()
    if not class_name and extra_tokens:
        class_name = extra_tokens[0]
        extra_tokens = extra_tokens[1:]

    # Deduplicate repeated class prefix words if adjacent
    if class_name:
        parts = class_name.split()
        seen = []
        for p in parts:
            if not seen or p.lower() != seen[-1].lower():
                seen.append(p)
        class_name = " ".join(seen)

    if not room_or_group and extra_tokens:
        room_or_group = " ".join(extra_tokens)

    num_m = re.search(r'\d+', class_name) if class_name else None
    year_group = f"Stage {num_m.group(0)}" if num_m else "Stage 7"

    # Level-aware code resolution for this cell
    if subject_name and class_name:
        resolved_code, _ = infer_level_cambridge_code(subject_name, class_name)
        if resolved_code:
            cambridge_code = resolved_code

    if class_name or subject_name:
        return {
            "class_name": class_name or "Grade 7",
            "year_group": year_group,
            "subject_raw": subject_raw or "SUBJ",
            "subject_name": subject_name or subject_raw or "General Subject",
            "cambridge_code": cambridge_code or "",
            "room": room_or_group,
            "raw_text": full_text,
        }
    return None


def parse_asc_pdfplumber(pdf_bytes, global_cambridge_subjects=None):
    """High-fidelity parser for aSc Timetables PDF exports with geometry, unmirroring, and double period handling."""
    slots = []
    detected_short_forms = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            if not tables:
                continue
            table = max(tables, key=lambda t: (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
            words = [dict(w) for w in page.extract_words()]
            cells = [tuple(c) for c in table.cells]

            # 1. Group rows by top coordinate
            rows_by_top = defaultdict(list)
            for c in cells:
                rows_by_top[round(c[1] / 3)].append(c)

            merged_rows = []
            used_keys = set()
            for k in sorted(rows_by_top):
                if k in used_keys:
                    continue
                block = list(rows_by_top[k])
                for k2 in sorted(rows_by_top):
                    if k2 > k and k2 - k <= 2 and k2 not in used_keys:
                        block += rows_by_top[k2]
                        used_keys.add(k2)
                used_keys.add(k)
                merged_rows.append(block)

            # 2. Find Header row containing 'Morning' or period digits
            header_row = None
            for r in merged_rows:
                texts = [cell_words_text(c, words) for c in r]
                if any("Morning" in t or re.search(r'\b(?:Period|Lesson)\s*\d', t, re.I) for t in texts):
                    header_row = r
                    break

            if header_row is None and merged_rows:
                for r in merged_rows:
                    texts = [cell_words_text(c, words) for c in r]
                    if sum(bool(re.search(r'\d{1,2}[:.]\d{2}', t)) for t in texts) >= 2:
                        header_row = r
                        break

            if header_row is None:
                continue

            periods = []
            for bbox in sorted(header_row, key=lambda c: c[0]):
                txt = cell_words_text(bbox, words)
                if not txt:
                    continue
                m = re.match(r"^(\d+)\s*(.*)$", txt)
                if m:
                    label, times = m.group(1), m.group(2)
                else:
                    parts = txt.split()
                    label, times = parts[0], " ".join(parts[1:])
                periods.append({"label": label, "times": times, "x0": bbox[0], "x1": bbox[2]})

            numeric_periods = [p for p in periods if p["label"].isdigit()]
            if not numeric_periods:
                numeric_periods = periods

            label_x_max = min(p["x0"] for p in numeric_periods)
            grid_x_max = max(p["x1"] for p in numeric_periods) + 5

            # 3. Detect day row bands with unmirror
            day_bands = []
            for bbox in sorted(cells, key=lambda c: c[1]):
                x0, top, x1, bottom = bbox
                if x0 < label_x_max - 2:
                    name = unmirror(cell_words_text(bbox, words))
                    if name and not any(db[0] == name for db in day_bands):
                        day_bands.append((name, top, bottom))

            # 4. Extract cells in day row bands and resolve double-period cell spans
            for name, band_top, band_bottom in day_bands:
                day_idx = DAYS_MAP.get(name.lower(), 0)
                for bbox in cells:
                    x0, top, x1, bottom = bbox
                    if x0 < label_x_max - 2 or x1 > grid_x_max:
                        continue
                    if band_top - 4 <= top and bottom <= band_bottom + 4:
                        inside_words = [
                            w for w in words
                            if x0 - 2 <= (w["x0"] + w["x1"]) / 2 < x1 + 2
                            and top - 2 <= (w["top"] + w["bottom"]) / 2 < bottom + 2
                        ]
                        inside_words.sort(key=lambda w: (round(w["top"] / 4), w["x0"]))
                        lines_dict = defaultdict(list)
                        for w in inside_words:
                            lines_dict[round(w["top"] / 4)].append(w["text"])
                        cell_lines = [" ".join(lines_dict[k]) for k in sorted(lines_dict.keys())]

                        decoded = decode_cell_entry(cell_lines, global_cambridge_subjects)
                        if not decoded:
                            continue

                        spanned = [
                            p for p in numeric_periods
                            if x0 - 2 <= (p["x0"] + p["x1"]) / 2 < x1 + 2
                        ]

                        if spanned:
                            start_time_str = "08:00"
                            end_time_str = "08:45"
                            first_p = spanned[0]
                            last_p = spanned[-1]

                            tm_first = TIME_RANGE_REGEX.search(first_p["times"])
                            if tm_first:
                                start_time_str = parse_time_str(tm_first.group("start_h"), tm_first.group("start_m"), tm_first.group("start_p"))
                            tm_last = TIME_RANGE_REGEX.search(last_p["times"])
                            if tm_last:
                                end_time_str = parse_time_str(tm_last.group("end_h"), tm_last.group("end_m"), tm_last.group("end_p"))

                            period_lbl = f"Period {first_p['label']}" if len(spanned) == 1 else f"Period {first_p['label']}–{last_p['label']}"
                            slot_id = f"slot-{len(slots) + 1}"
                            detected_short_forms.add(decoded["subject_raw"].upper())

                            slots.append({
                                "id": slot_id,
                                "day_of_week": day_idx,
                                "day_name": name,
                                "start_time": start_time_str,
                                "end_time": end_time_str,
                                "period_label": period_lbl,
                                "class_name": decoded["class_name"],
                                "year_group": decoded["year_group"],
                                "subject_raw": decoded["subject_raw"],
                                "subject_name": decoded["subject_name"],
                                "cambridge_code": decoded.get("cambridge_code", ""),
                                "room": decoded["room"],
                                "raw_line": " \n".join(cell_lines),
                            })

    return slots, detected_short_forms


def parse_timetable_pdf(pdf_file_or_bytes, school=None):
    """Main timetable PDF parsing entry point with multi-tier fallbacks."""
    if isinstance(pdf_file_or_bytes, bytes):
        raw_bytes = pdf_file_or_bytes
    elif hasattr(pdf_file_or_bytes, "read"):
        raw_bytes = pdf_file_or_bytes
    else:
        raw_bytes = pdf_file_or_bytes

    global_cambridge_subjects = list(
        SchemeOfWork.objects.values_list("subject_name", "subject_code").distinct()
    )

    detected_short_forms = set()
    slots = []
    unparsed_lines = []

    # Tier 1: aSc pdfplumber table & geometry extractor
    try:
        asc_slots, asc_short_forms = parse_asc_pdfplumber(raw_bytes, global_cambridge_subjects)
        if asc_slots:
            slots = asc_slots
            detected_short_forms.update(asc_short_forms)
    except Exception as exc:
        print(f"Tier 1 aSc pdfplumber parser note: {exc}")

    # Tier 2: 2D Spatial coordinate parser via pypdfium2
    if not slots:
        pdf_doc = None
        try:
            pdf_doc = pdfium.PdfDocument(raw_bytes)
            for page_idx in range(len(pdf_doc)):
                page = pdf_doc[page_idx]
                tp = page.get_textpage()
                num_rects = tp.count_rects()

                items = []
                for i in range(num_rects):
                    r = tp.get_rect(i)
                    txt = tp.get_text_bounded(*r).strip()
                    if txt:
                        items.append({
                            "text": txt,
                            "left": r[0], "bottom": r[1], "right": r[2], "top": r[3],
                            "cx": (r[0] + r[2]) / 2, "cy": (r[1] + r[3]) / 2
                        })

                day_cols = []
                for item in items:
                    txt_unmirrored = unmirror(item["text"])
                    if txt_unmirrored:
                        d_idx = DAYS_MAP.get(txt_unmirrored.lower(), 0)
                        if not any(abs(c["cx"] - item["cx"]) < 20 for c in day_cols):
                            day_cols.append({
                                "day_idx": d_idx,
                                "day_name": txt_unmirrored,
                                "cx": item["cx"],
                                "cy": item["cy"],
                            })

                day_cols.sort(key=lambda c: c["cx"])

                time_rows = []
                for item in items:
                    t_m = TIME_RANGE_REGEX.search(item["text"])
                    if t_m:
                        st = parse_time_str(t_m.group("start_h"), t_m.group("start_m"), t_m.group("start_p"))
                        et = parse_time_str(t_m.group("end_h"), t_m.group("end_m"), t_m.group("end_p"))
                        if not any(abs(tr["cy"] - item["cy"]) < 10 for tr in time_rows):
                            time_rows.append({
                                "text": item["text"],
                                "cy": item["cy"],
                                "start_time": st,
                                "end_time": et,
                            })

                time_rows.sort(key=lambda r: -r["cy"])

                if len(day_cols) >= 2 and time_rows:
                    col_w = (day_cols[1]["cx"] - day_cols[0]["cx"]) if len(day_cols) > 1 else 100
                    row_h = (time_rows[0]["cy"] - time_rows[1]["cy"]) if len(time_rows) > 1 else 50
                    if row_h <= 0:
                        row_h = 50

                    for r_idx, tr in enumerate(time_rows):
                        p_label = f"Period {r_idx + 1}"
                        for it in items:
                            if abs(it["cy"] - tr["cy"]) < row_h / 2:
                                p_m = PERIOD_REGEX.search(it["text"])
                                if p_m:
                                    p_label = f"Period {p_m.group(1)}"
                                    break

                        for col in day_cols:
                            cell_items = [
                                it for it in items
                                if abs(it["cx"] - col["cx"]) < col_w / 2 and abs(it["cy"] - tr["cy"]) < row_h / 2
                            ]
                            cell_items.sort(key=lambda x: -x["cy"])
                            cell_text = " \n".join(x["text"] for x in cell_items).strip()

                            cell_result = decode_cell_entry(cell_text, global_cambridge_subjects)
                            if cell_result:
                                slot_id = f"slot-{len(slots) + 1}"
                                detected_short_forms.add(cell_result["subject_raw"].upper())
                                slots.append({
                                    "id": slot_id,
                                    "day_of_week": col["day_idx"],
                                    "day_name": col["day_name"],
                                    "start_time": tr["start_time"],
                                    "end_time": tr["end_time"],
                                    "period_label": p_label,
                                    "class_name": cell_result["class_name"],
                                    "year_group": cell_result["year_group"],
                                    "subject_raw": cell_result["subject_raw"],
                                    "subject_name": cell_result["subject_name"],
                                    "cambridge_code": cell_result.get("cambridge_code", ""),
                                    "room": cell_result["room"],
                                    "raw_line": cell_text.replace("\n", " "),
                                })
        except Exception as exc:
            print(f"Tier 2 spatial parser exception: {exc}")
        finally:
            if pdf_doc:
                try:
                    pdf_doc.close()
                except Exception:
                    pass

    # Build Subject Mappings enriched with taught Classes & level-accurate Cambridge codes
    subject_mappings = []
    for sf in sorted(detected_short_forms):
        if not sf or sf == "SUBJ":
            continue

        matching_slots = [s for s in slots if s["subject_raw"].upper() == sf.upper()]
        classes_taught = []
        for s in matching_slots:
            cls = s["class_name"].strip()
            if cls and cls not in classes_taught:
                classes_taught.append(cls)

        classes_str = ", ".join(classes_taught) if classes_taught else "General"

        # Determine subject name
        lookup_entry = KNOWN_CAMBRIDGE_SUBJECTS.get(sf.upper())
        if lookup_entry:
            suggested_name = lookup_entry[0]
        else:
            suggested_name = sf.title()

        # Infer level-accurate Cambridge code from the classes taking this subject
        suggested_code, fw_name = infer_level_cambridge_code(suggested_name, classes_taught)

        # Update matching slots with this resolved code
        for s in matching_slots:
            if not s.get("cambridge_code") or s.get("cambridge_code") in ("0478", "0860", "0059", "9618"):
                slot_code, _ = infer_level_cambridge_code(s["subject_name"], s["class_name"])
                if slot_code:
                    s["cambridge_code"] = slot_code
                else:
                    s["cambridge_code"] = suggested_code

        subject_mappings.append({
            "raw_code": sf,
            "detected_name": suggested_name,
            "cambridge_code": suggested_code or sf,
            "classes_taught": classes_taught,
            "classes_taught_str": classes_str,
            "framework_name": fw_name,
        })

    return {
        "detected_subject_mappings": subject_mappings,
        "slots": slots,
        "unparsed_lines": unparsed_lines[:20],
        "total_slots": len(slots),
    }


def commit_teacher_timetable(*, timetable, subject_mappings, confirmed_slots, actor):
    """Commit confirmed timetable slots, auto-provisioning Subjects, Classes, Assignments, and Schedule Slots."""
    if timetable.teacher_id != actor.id:
        raise PermissionDenied("You can only confirm your own timetable.")

    school = timetable.school
    today = timezone.localdate()

    with transaction.atomic():
        resolved_subjects = {}

        for raw_code, mapping in subject_mappings.items():
            raw_key = (raw_code or "").strip().upper()
            subj_name = (mapping.get("subject_name") or raw_code).strip()
            cam_code = (mapping.get("cambridge_code") or "").strip()

            if not cam_code:
                lookup = KNOWN_CAMBRIDGE_SUBJECTS.get(raw_key)
                if lookup:
                    cam_code = lookup[1]
                else:
                    scheme = SchemeOfWork.objects.filter(subject_name__icontains=subj_name, is_active=True).first()
                    if scheme:
                        cam_code = scheme.subject_code

            subject = None
            if cam_code:
                subject = Subject.all_objects.filter(school=school, cambridge_code__iexact=cam_code).first()

            if not subject and subj_name:
                subject = Subject.all_objects.filter(school=school, name__iexact=subj_name).first()

            if not subject and raw_code:
                subject = Subject.all_objects.filter(school=school, code__iexact=raw_code).first()

            if not subject:
                code_to_use = raw_code if len(raw_code) <= 32 else raw_code[:32]
                subject = Subject.all_objects.create(
                    school=school,
                    name=subj_name,
                    code=code_to_use,
                    cambridge_code=cam_code or code_to_use,
                    is_active=True,
                )

            resolved_subjects[raw_key] = subject
            if subj_name.upper() not in resolved_subjects:
                resolved_subjects[subj_name.upper()] = subject

        # 2. Resolve and provision Classes
        resolved_classes = {}
        for slot in confirmed_slots:
            cls_name = (slot.get("class_name") or "Grade 7").strip()
            cls_key = cls_name.lower()
            if cls_key not in resolved_classes:
                yr_group = (slot.get("year_group") or "").strip()
                school_class = SchoolClass.all_objects.filter(school=school, name__iexact=cls_name).first()
                if not school_class:
                    school_class = SchoolClass.all_objects.create(
                        school=school,
                        name=cls_name,
                        year_group=yr_group,
                        boys_count=12,
                        girls_count=12,
                        is_active=True,
                    )
                resolved_classes[cls_key] = school_class

        # 3. Resolve and provision TeacherAssignments
        resolved_assignments = {}
        created_assignments = []

        for slot in confirmed_slots:
            raw_subj = (slot.get("subject_raw") or slot.get("subject_name") or "Subject").strip().upper()
            subject = resolved_subjects.get(raw_subj)
            if not subject:
                slot_subj_name = (slot.get("subject_name") or raw_subj).strip()
                subject = Subject.all_objects.filter(school=school, name__iexact=slot_subj_name).first()
                if not subject:
                    cam_code = slot.get("cambridge_code", "")
                    if not cam_code:
                        lookup = KNOWN_CAMBRIDGE_SUBJECTS.get(raw_subj)
                        if lookup:
                            cam_code = lookup[1]
                    subject = Subject.all_objects.create(
                        school=school,
                        name=slot_subj_name,
                        code=raw_subj[:32],
                        cambridge_code=cam_code or raw_subj[:32],
                        is_active=True,
                    )
                resolved_subjects[raw_subj] = subject

            cls_name = (slot.get("class_name") or "Grade 7").strip()
            school_class = resolved_classes[cls_name.lower()]

            pair_key = (subject.id, school_class.id)
            if pair_key not in resolved_assignments:
                assignment = TeacherAssignment.all_objects.filter(
                    school=school,
                    teacher=actor,
                    subject=subject,
                    school_class=school_class,
                    is_active=True,
                ).first()

                if not assignment:
                    assignment = TeacherAssignment.all_objects.create(
                        school=school,
                        teacher=actor,
                        subject=subject,
                        school_class=school_class,
                        effective_from=today,
                        is_active=True,
                    )
                    created_assignments.append(assignment)
                resolved_assignments[pair_key] = assignment

        # 4. Create TeacherScheduleSlots
        TeacherScheduleSlot.all_objects.filter(timetable=timetable).delete()
        created_slots = []

        for slot in confirmed_slots:
            raw_subj = (slot.get("subject_raw") or slot.get("subject_name") or "Subject").strip().upper()
            subject = resolved_subjects[raw_subj]
            cls_name = (slot.get("class_name") or "Grade 7").strip()
            school_class = resolved_classes[cls_name.lower()]
            assignment = resolved_assignments[(subject.id, school_class.id)]

            day_of_week = int(slot.get("day_of_week", 0))
            st_str = slot.get("start_time", "08:00")
            et_str = slot.get("end_time", "08:45")

            try:
                st_parts = [int(p) for p in st_str.split(":")[:2]]
                start_t = time(st_parts[0], st_parts[1])
            except Exception:
                start_t = time(8, 0)

            try:
                et_parts = [int(p) for p in et_str.split(":")[:2]]
                end_t = time(et_parts[0], et_parts[1])
            except Exception:
                end_t = time(8, 45)

            if end_t <= start_t:
                end_t = time(min(23, start_t.hour + (1 if start_t.minute + 45 >= 60 else 0)), (start_t.minute + 45) % 60)

            slot_obj = TeacherScheduleSlot.all_objects.create(
                school=school,
                timetable=timetable,
                assignment=assignment,
                day_of_week=day_of_week,
                start_time=start_t,
                end_time=end_t,
                period_label=slot.get("period_label", ""),
                room=slot.get("room", ""),
                is_active=True,
            )
            created_slots.append(slot_obj)

        timetable.status = TeacherTimetable.Status.CONFIRMED
        timetable.confirmed_at = timezone.now()
        timetable.parsed_data = {
            "subject_mappings": subject_mappings,
            "confirmed_slots": confirmed_slots,
            "total_slots": len(created_slots),
        }
        timetable.save(update_fields=["status", "confirmed_at", "parsed_data", "updated_at"])

        AuditLog.all_objects.create(
            school=school,
            actor=actor,
            action="TEACHER_TIMETABLE_CONFIRMED",
            target_type="TeacherTimetable",
            target_id=str(timetable.id),
            metadata={
                "slots_count": len(created_slots),
                "assignments_created": len(created_assignments),
            },
        )

    return {
        "timetable": timetable,
        "assignments": list(resolved_assignments.values()),
        "assignments_created_count": len(created_assignments),
        "slots_created_count": len(created_slots),
    }
