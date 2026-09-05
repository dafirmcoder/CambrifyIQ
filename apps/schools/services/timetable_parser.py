"""Timetable PDF parsing engine.

Extracts weekly lesson schedule slots (days, times, classes, subjects, rooms) from uploaded teacher timetable PDFs.
Supports both tabular/grid timetables and list-based schedules.
"""

import io
import re
from datetime import time
from pypdf import PdfReader
from apps.curriculum.models import SchemeOfWork
from apps.schools.models import Subject, SchoolClass

DAYS_MAP = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

DAYS_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Common subject abbreviations and their standard Cambridge/School names
KNOWN_SUBJECT_SHORT_FORMS = {
    "COMP": "Computing",
    "CMP": "Computing",
    "COMPUTING": "Computing",
    "DL": "Digital Literacy",
    "D.L.": "Digital Literacy",
    "DIGLIT": "Digital Literacy",
    "CS": "Computer Science",
    "C.S.": "Computer Science",
    "ICT": "Information and Communication Technology",
    "I.C.T.": "Information and Communication Technology",
    "IT": "Information and Communication Technology",
    "MATH": "Mathematics",
    "MATHS": "Mathematics",
    "MTH": "Mathematics",
    "MAT": "Mathematics",
    "ENG": "English",
    "ENGL": "English",
    "ESL": "English as a Second Language",
    "SCI": "Science",
    "SC": "Science",
    "BIO": "Biology",
    "BIOL": "Biology",
    "CHEM": "Chemistry",
    "CHM": "Chemistry",
    "PHY": "Physics",
    "PHYS": "Physics",
    "BUS": "Business Studies",
    "BST": "Business Studies",
    "BUS ST": "Business Studies",
    "ECO": "Economics",
    "ECON": "Economics",
    "ACC": "Accounting",
    "ACCT": "Accounting",
    "GEO": "Geography",
    "GEOG": "Geography",
    "HIST": "History",
    "HIS": "History",
    "ART": "Art and Design",
    "MUSIC": "Music",
    "PE": "Physical Education",
    "P.E.": "Physical Education",
    "GP": "Global Perspectives",
}

# Regex for time ranges: e.g. 08:00 - 08:45, 8:00am - 8:40am, 8.00 - 8.45, 08:00 to 08:45
TIME_RANGE_REGEX = re.compile(
    r'(?P<start_h>\d{1,2})[:.](?P<start_m>\d{2})\s*(?P<start_p>am|pm)?\s*(?:-|–|to)\s*(?P<end_h>\d{1,2})[:.](?P<end_m>\d{2})\s*(?P<end_p>am|pm)?',
    re.IGNORECASE
)

# Regex for period headers: e.g. Period 1, Lesson 2, P1, P2
PERIOD_REGEX = re.compile(r'(?:Period|Lesson|P)\s*(\d{1,2})', re.IGNORECASE)

# Regex for class/grade identifiers
CLASS_REGEX = re.compile(
    r'(?:Grade|Year|Stage|Form|Class|Yr|Gr|Stg)\s*([0-9]{1,2}\s*[A-Za-z0-9\-_]*|[0-9]{1,2}[A-Za-z])',
    re.IGNORECASE
)


def parse_time_str(hour_str, minute_str, period_str=None):
    h = int(hour_str)
    m = int(minute_str)
    if period_str:
        p = period_str.lower()
        if p == "pm" and h < 12:
            h += 12
        elif p == "am" and h == 12:
            h = 0
    # Infer PM for typical school hours if h < 7 (e.g. 1:00 - 1:45 PM)
    elif h < 7:
        h += 12
    return f"{h:02d}:{m:02d}"


def extract_raw_text_from_pdf(pdf_file_or_bytes):
    """Extract text lines from an uploaded PDF document using pypdf."""
    if isinstance(pdf_file_or_bytes, bytes):
        reader = PdfReader(io.BytesIO(pdf_file_or_bytes))
    elif hasattr(pdf_file_or_bytes, "read"):
        reader = PdfReader(pdf_file_or_bytes)
    else:
        reader = PdfReader(pdf_file_or_bytes)

    pages_text = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        pages_text.append(txt)
    return "\n".join(pages_text)


def parse_timetable_pdf(pdf_file, school=None):
    """Parse timetable PDF and return detected subject mappings, schedule slots, and unparsed elements."""
    raw_text = extract_raw_text_from_pdf(pdf_file)
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    # Collect known school subjects if school provided
    school_subjects = []
    if school:
        school_subjects = list(Subject.objects.filter(school=school, is_active=True))

    detected_short_forms = set()
    slots = []
    current_day = None
    unparsed_lines = []

    # Strategy 1: Line-by-line structured/card parsing
    for line in lines:
        line_lower = line.lower()

        # Check if line indicates a Day header
        day_match = None
        for day_word, day_idx in DAYS_MAP.items():
            # Check for exact word or start of line
            if re.search(r'' + re.escape(day_word) + r'', line_lower):
                day_match = day_idx
                break

        if day_match is not None and len(line.split()) <= 3:
            current_day = day_match
            continue

        # Check for time range in the line
        time_m = TIME_RANGE_REGEX.search(line)
        if time_m:
            start_t = parse_time_str(time_m.group("start_h"), time_m.group("start_m"), time_m.group("start_p"))
            end_t = parse_time_str(time_m.group("end_h"), time_m.group("end_m"), time_m.group("end_p"))

            # Determine day if mentioned in same line
            line_day = current_day if current_day is not None else 0
            if day_match is not None:
                line_day = day_match

            # Extract Period
            period_m = PERIOD_REGEX.search(line)
            period_label = f"Period {period_m.group(1)}" if period_m else ""

            # Extract Class name
            class_m = CLASS_REGEX.search(line)
            class_name = ""
            year_group = ""
            if class_m:
                matched_cls = class_m.group(0).strip()
                class_name = matched_cls
                # Infer year group (e.g. Grade 7A -> Stage 7 / Year 7)
                num_m = re.search(r'\d+', matched_cls)
                if num_m:
                    year_group = f"Stage {num_m.group(0)}"
            else:
                # Standalone pattern like 7A, 8B, 9C, 10-1
                alt_class_m = re.search(r'([0-9]{1,2}[A-Za-z])', line)
                if alt_class_m:
                    class_name = f"Grade {alt_class_m.group(1)}"
                    num_m = re.search(r'\d+', alt_class_m.group(1))
                    if num_m:
                        year_group = f"Stage {num_m.group(0)}"

            # Extract Subject / Short form
            # Check for known abbreviations in line
            detected_subj = ""
            detected_raw = ""
            words = re.findall(r'[A-Za-z0-9.]+', line)
            for w in words:
                w_upper = w.upper().replace(".", "")
                if w_upper in KNOWN_SUBJECT_SHORT_FORMS:
                    detected_raw = w
                    detected_subj = KNOWN_SUBJECT_SHORT_FORMS[w_upper]
                    detected_short_forms.add(w.upper())
                    break

            # If not found via abbreviation, check school subjects
            if not detected_subj and school_subjects:
                for s in school_subjects:
                    if s.name.lower() in line_lower or (s.code and s.code.lower() in line_lower):
                        detected_subj = s.name
                        detected_raw = s.code or s.name
                        detected_short_forms.add(detected_raw.upper())
                        break

            # Room extraction (e.g. Lab 1, Room 102, R4)
            room_m = re.search(r'(?:Room|Lab|Rm|Studio|Hall)\s*([A-Za-z0-9\-_]+)', line, re.IGNORECASE)
            room = room_m.group(0) if room_m else ""

            if class_name or detected_subj:
                slot_id = f"slot-{len(slots) + 1}"
                slots.append({
                    "id": slot_id,
                    "day_of_week": line_day,
                    "day_name": DAYS_NAMES[line_day],
                    "start_time": start_t,
                    "end_time": end_t,
                    "period_label": period_label or f"Period {len(slots) + 1}",
                    "class_name": class_name or "Grade 7",
                    "year_group": year_group or "Stage 7",
                    "subject_raw": detected_raw or "Subject",
                    "subject_name": detected_subj or detected_raw or "General Subject",
                    "room": room,
                    "raw_line": line,
                })
            else:
                unparsed_lines.append(line)
        else:
            unparsed_lines.append(line)

    # If no slots found via line-by-line, parse grid/table patterns
    if not slots and lines:
        # Fallback grid parser: look for lines with subjects and classes
        for idx, line in enumerate(lines):
            for day_word, day_idx in DAYS_MAP.items():
                if day_word in line.lower() and len(line.split()) < 4:
                    current_day = day_idx
                    break

            for abbrev, full_subj in KNOWN_SUBJECT_SHORT_FORMS.items():
                if re.search(r'' + re.escape(abbrev) + r'', line, re.IGNORECASE):
                    detected_short_forms.add(abbrev)
                    class_m = CLASS_REGEX.search(line) or re.search(r'([0-9]{1,2}[A-Za-z])', line)
                    class_name = class_m.group(0) if class_m else "Class 1"
                    num_m = re.search(r'\d+', class_name)
                    year_group = f"Stage {num_m.group(0)}" if num_m else "Stage 7"

                    slot_id = f"slot-{len(slots) + 1}"
                    line_day = current_day if current_day is not None else 0
                    slots.append({
                        "id": slot_id,
                        "day_of_week": line_day,
                        "day_name": DAYS_NAMES[line_day],
                        "start_time": "08:00",
                        "end_time": "08:45",
                        "period_label": f"Period {len(slots) + 1}",
                        "class_name": class_name,
                        "year_group": year_group,
                        "subject_raw": abbrev,
                        "subject_name": full_subj,
                        "room": "",
                        "raw_line": line,
                    })

    # Prepare subject mappings with smart suggestion dropdown choices
    subject_mappings = []
    for sf in sorted(detected_short_forms):
        suggested_name = KNOWN_SUBJECT_SHORT_FORMS.get(sf, sf.title())
        suggested_code = ""

        # Match with Cambridge Scheme
        matching_scheme = SchemeOfWork.objects.filter(subject_name__icontains=suggested_name, is_active=True).first()
        if matching_scheme:
            suggested_code = matching_scheme.subject_code

        # Match with School Subject if exists
        matched_school_subject_id = None
        if school_subjects:
            for subj in school_subjects:
                if subj.name.lower() == suggested_name.lower() or (subj.code and subj.code.upper() == sf):
                    matched_school_subject_id = str(subj.id)
                    break

        subject_mappings.append({
            "raw_code": sf,
            "detected_name": suggested_name,
            "suggested_subject_id": matched_school_subject_id,
            "cambridge_code": suggested_code or sf,
        })

    return {
        "detected_subject_mappings": subject_mappings,
        "slots": slots,
        "unparsed_lines": unparsed_lines[:20],
        "total_slots": len(slots),
    }
