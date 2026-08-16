"""Validated curriculum import with a quality report (plan 7.1, Phase 1).

Accepts a flat CSV/row structure that mirrors how schools keep schemes in
spreadsheets. One row describes one learning objective and carries the topic and
sub-topic it belongs to, so the hierarchy is built as rows are read:

    topic_code, topic_title, subtopic_code, subtopic_title, lo_code, lo_text

The import is atomic and never partially applies: any row error aborts the whole
run and the caller receives a report listing every problem found.
"""

import csv
from dataclasses import dataclass, field
from io import StringIO

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.curriculum.models import LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.curriculum.services import assert_can_edit_curriculum
from apps.schools.models import AuditLog

REQUIRED_COLUMNS = ("topic_code", "topic_title", "lo_code", "lo_text")
OPTIONAL_COLUMNS = ("subtopic_code", "subtopic_title", "topic_sequence", "lo_sequence")


@dataclass
class ImportReport:
    """Outcome of one import run."""

    topics_created: int = 0
    subtopics_created: int = 0
    objectives_created: int = 0
    rows_read: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {
            "ok": self.ok,
            "rows_read": self.rows_read,
            "topics_created": self.topics_created,
            "subtopics_created": self.subtopics_created,
            "objectives_created": self.objectives_created,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def parse_csv(text):
    """Read CSV text into normalised dict rows."""
    reader = csv.DictReader(StringIO(text))
    rows = []
    for raw in reader:
        rows.append(
            {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        )
    return rows


def validate_rows(rows):
    """Check structure, duplicates and relationships before any write."""
    errors = []
    warnings = []
    if not rows:
        errors.append("The file contains no data rows.")
        return errors, warnings

    missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}.")
        return errors, warnings

    seen_objectives = {}
    topic_titles = {}
    subtopic_parents = {}

    for index, row in enumerate(rows, start=2):  # Row 1 is the header.
        for column in REQUIRED_COLUMNS:
            if not row.get(column):
                errors.append(f"Row {index}: {column} is required.")

        topic_code = row.get("topic_code")
        lo_code = row.get("lo_code")
        subtopic_code = row.get("subtopic_code")

        if topic_code and row.get("topic_title"):
            existing = topic_titles.setdefault(topic_code, row["topic_title"])
            if existing != row["topic_title"]:
                warnings.append(
                    f"Row {index}: topic {topic_code} has more than one title; "
                    f"keeping '{existing}'."
                )

        if subtopic_code:
            if not row.get("subtopic_title"):
                errors.append(f"Row {index}: subtopic_title is required with subtopic_code.")
            parent = subtopic_parents.setdefault(subtopic_code, topic_code)
            if parent != topic_code:
                errors.append(
                    f"Row {index}: sub-topic {subtopic_code} is claimed by both "
                    f"{parent} and {topic_code}."
                )

        if lo_code:
            key = (topic_code, lo_code)
            if key in seen_objectives:
                errors.append(
                    f"Row {index}: objective {lo_code} is duplicated in topic {topic_code} "
                    f"(first seen on row {seen_objectives[key]})."
                )
            else:
                seen_objectives[key] = index

    return errors, warnings


@transaction.atomic
def import_curriculum(*, membership, scheme, rows, dry_run=False):
    """Import objective rows into a scheme, returning an ``ImportReport``."""
    assert_can_edit_curriculum(membership)
    if scheme.school_id != membership.school_id:
        raise PermissionDenied("That scheme belongs to another school.")
    if scheme.status == SchemeOfWork.Status.ARCHIVED:
        raise PermissionDenied("An archived scheme cannot be imported into.")

    report = ImportReport(rows_read=len(rows))
    report.errors, report.warnings = validate_rows(rows)
    if report.errors:
        return report

    topics = {}
    subtopics = {}

    for position, row in enumerate(rows, start=1):
        topic = topics.get(row["topic_code"])
        if topic is None:
            topic, created = Topic.all_objects.get_or_create(
                scheme=scheme,
                code=row["topic_code"],
                defaults={
                    "school_id": scheme.school_id,
                    "title": row["topic_title"],
                    "sequence": int(row.get("topic_sequence") or len(topics) + 1),
                },
            )
            topics[row["topic_code"]] = topic
            report.topics_created += int(created)

        subtopic = None
        if row.get("subtopic_code"):
            key = (row["topic_code"], row["subtopic_code"])
            subtopic = subtopics.get(key)
            if subtopic is None:
                subtopic, created = Subtopic.all_objects.get_or_create(
                    topic=topic,
                    code=row["subtopic_code"],
                    defaults={
                        "school_id": scheme.school_id,
                        "title": row["subtopic_title"],
                        "sequence": len(subtopics) + 1,
                    },
                )
                subtopics[key] = subtopic
                report.subtopics_created += int(created)

        _, created = LearningObjective.all_objects.get_or_create(
            school_id=scheme.school_id,
            topic=topic,
            code=row["lo_code"],
            defaults={
                "subtopic": subtopic,
                "text": row["lo_text"],
                "sequence": int(row.get("lo_sequence") or position),
            },
        )
        report.objectives_created += int(created)

    AuditLog.all_objects.create(
        school_id=scheme.school_id,
        actor_id=membership.user_id,
        action="curriculum.imported",
        target_type="scheme_of_work",
        target_id=str(scheme.pk),
        metadata={**report.as_dict(), "dry_run": dry_run},
    )

    if dry_run:
        transaction.set_rollback(True)
    return report
