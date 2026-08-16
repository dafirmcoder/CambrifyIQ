"""Curriculum import tests — plan section 7.1 (validated imports and reports)."""

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.importers import import_curriculum, parse_csv, validate_rows
from apps.curriculum.models import LearningObjective, Subtopic, Topic
from apps.curriculum.tests import CurriculumFixture
from apps.schools.models import AuditLog, Membership

CSV_TEXT = """topic_code,topic_title,subtopic_code,subtopic_title,lo_code,lo_text
T5,Chemistry basics,T5.1,Atoms,8Cs.01,Describe the structure of an atom.
T5,Chemistry basics,T5.1,Atoms,8Cs.02,Explain isotopes.
T5,Chemistry basics,,,8Cs.03,Recall the periodic table groups.
T6,Biology basics,T6.1,Cells,8Bs.01,Identify plant cell parts.
"""


class ParsingTests(TestCase):
    def test_parse_csv_normalises_headers_and_values(self):
        rows = parse_csv(CSV_TEXT)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["topic_code"], "T5")
        self.assertEqual(rows[2]["subtopic_code"], "")

    def test_missing_required_column_is_reported(self):
        errors, _ = validate_rows([{"topic_code": "T1", "topic_title": "X"}])
        self.assertTrue(any("Missing required columns" in item for item in errors))

    def test_empty_file_is_reported(self):
        errors, _ = validate_rows([])
        self.assertTrue(any("no data rows" in item for item in errors))

    def test_duplicate_objective_is_reported(self):
        rows = parse_csv(
            "topic_code,topic_title,lo_code,lo_text\n"
            "T1,Forces,8Ps.01,First.\n"
            "T1,Forces,8Ps.01,Repeat.\n"
        )
        errors, _ = validate_rows(rows)
        self.assertTrue(any("duplicated" in item for item in errors))

    def test_subtopic_claimed_by_two_topics_is_reported(self):
        rows = parse_csv(
            "topic_code,topic_title,subtopic_code,subtopic_title,lo_code,lo_text\n"
            "T1,Forces,S1,Speed,8Ps.01,First.\n"
            "T2,Energy,S1,Speed,8Pe.01,Second.\n"
        )
        errors, _ = validate_rows(rows)
        self.assertTrue(any("claimed by both" in item for item in errors))

    def test_conflicting_topic_title_is_a_warning_not_an_error(self):
        rows = parse_csv(
            "topic_code,topic_title,lo_code,lo_text\n"
            "T1,Forces,8Ps.01,First.\n"
            "T1,Forces and motion,8Ps.02,Second.\n"
        )
        errors, warnings = validate_rows(rows)
        self.assertEqual(errors, [])
        self.assertTrue(any("more than one title" in item for item in warnings))


class ImportTests(TestCase, CurriculumFixture):
    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")
        self.school = self.data["school"]
        self.coordinator = self.add_leader(
            self.data, Membership.Role.COORDINATOR, "coord@lis.example"
        )

    def test_import_builds_the_hierarchy(self):
        with tenant_scope(self.school.pk):
            report = import_curriculum(
                membership=self.coordinator,
                scheme=self.data["scheme"],
                rows=parse_csv(CSV_TEXT),
            )
            self.assertTrue(report.ok)
            self.assertEqual(report.topics_created, 2)
            self.assertEqual(report.subtopics_created, 2)
            self.assertEqual(report.objectives_created, 4)

            topic = Topic.objects.get(code="T5")
            self.assertEqual(topic.title, "Chemistry basics")
            self.assertEqual(Subtopic.objects.filter(topic=topic).count(), 1)
            standalone = LearningObjective.objects.get(code="8Cs.03")
            self.assertIsNone(standalone.subtopic_id)

    def test_import_is_idempotent(self):
        with tenant_scope(self.school.pk):
            rows = parse_csv(CSV_TEXT)
            import_curriculum(membership=self.coordinator, scheme=self.data["scheme"], rows=rows)
            second = import_curriculum(
                membership=self.coordinator, scheme=self.data["scheme"], rows=rows
            )
            self.assertEqual(second.objectives_created, 0)
            self.assertEqual(LearningObjective.objects.filter(code="8Cs.01").count(), 1)

    def test_dry_run_writes_nothing(self):
        with tenant_scope(self.school.pk):
            report = import_curriculum(
                membership=self.coordinator,
                scheme=self.data["scheme"],
                rows=parse_csv(CSV_TEXT),
                dry_run=True,
            )
        self.assertTrue(report.ok)
        self.assertEqual(report.objectives_created, 4)
        with tenant_scope(self.school.pk):
            self.assertFalse(Topic.objects.filter(code="T5").exists())

    def test_invalid_rows_abort_the_whole_run(self):
        rows = parse_csv(
            "topic_code,topic_title,lo_code,lo_text\n"
            "T7,Good,8Ps.10,Fine.\n"
            "T7,Good,8Ps.10,Duplicate.\n"
        )
        with tenant_scope(self.school.pk):
            report = import_curriculum(
                membership=self.coordinator, scheme=self.data["scheme"], rows=rows
            )
            self.assertFalse(report.ok)
            self.assertFalse(Topic.objects.filter(code="T7").exists())

    def test_teacher_cannot_import(self):
        teacher = self.add_teacher(self.data, "teacher@lis.example")
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            import_curriculum(
                membership=teacher, scheme=self.data["scheme"], rows=parse_csv(CSV_TEXT)
            )

    def test_cross_school_scheme_is_rejected(self):
        other = self.build_school("Beta Cambridge", "BETA")
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            import_curriculum(
                membership=self.coordinator,
                scheme=other["scheme"],
                rows=parse_csv(CSV_TEXT),
            )

    def test_import_is_audited(self):
        with tenant_scope(self.school.pk):
            import_curriculum(
                membership=self.coordinator,
                scheme=self.data["scheme"],
                rows=parse_csv(CSV_TEXT),
            )
        self.assertTrue(
            AuditLog.all_objects.filter(school=self.school, action="curriculum.imported").exists()
        )
