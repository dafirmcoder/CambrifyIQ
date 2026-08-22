from datetime import date
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)


class ImportFrameworkTests(TestCase):
    def setUp(self):
        self.mock_wb = MagicMock()
        self.mock_ws = MagicMock()
        self.mock_wb.sheetnames = ["Stage 1"]
        self.mock_wb.__getitem__.return_value = self.mock_ws

    @patch("apps.curriculum.management.commands.import_framework.openpyxl.load_workbook")
    @patch("apps.curriculum.management.commands.import_framework.Path.exists")
    def test_import_new_scheme_with_subtopics(self, mock_exists, mock_load):
        mock_exists.return_value = True
        mock_load.return_value = self.mock_wb

        # Simulating English layout
        self.mock_ws.iter_rows.return_value = [
            (["English"],),
            (["Stage 1"],),
            (["Reading"],),
            (["Word structure"],),
            (
                ["1Rw.01", "Know the name..."]
            ),  # wait, the script expects the code and text in the same column!
        ]

        # The script joins them, let's just put it as a single string as it is in the real file
        self.mock_ws.iter_rows.return_value = [
            ("English",),
            ("Stage 1",),
            ("Reading",),
            ("Word structure",),
            ("1Rw.01 Know the name of each letter",),
            ("Writing",),
            ("1W.01 Write a sentence",),
        ]

        call_command("import_framework", "0058 English.xlsx")

        self.assertTrue(CurriculumFramework.objects.filter(name="Cambridge Primary").exists())
        scheme = SchemeOfWork.objects.get(subject_code="0058", year_group="Stage 1")
        self.assertEqual(scheme.version, 1)

        topics = list(Topic.objects.filter(scheme=scheme).order_by("sequence"))
        self.assertEqual(len(topics), 2)
        self.assertEqual(topics[0].title, "Reading")
        self.assertEqual(topics[1].title, "Writing")

        subtopics = list(Subtopic.objects.filter(topic=topics[0]))
        self.assertEqual(len(subtopics), 1)
        self.assertEqual(subtopics[0].title, "Word structure")

        los = list(LearningObjective.objects.filter(scheme=scheme).order_by("sequence"))
        self.assertEqual(len(los), 2)
        self.assertEqual(los[0].code, "1Rw.01")
        self.assertEqual(los[0].text, "Know the name of each letter")
        self.assertEqual(los[0].topic, topics[0])
        self.assertEqual(los[0].subtopic, subtopics[0])

        self.assertEqual(los[1].code, "1W.01")
        self.assertEqual(los[1].text, "Write a sentence")
        self.assertEqual(los[1].topic, topics[1])
        self.assertIsNone(los[1].subtopic)

    @patch("apps.curriculum.management.commands.import_framework.openpyxl.load_workbook")
    @patch("apps.curriculum.management.commands.import_framework.Path.exists")
    def test_import_idempotent_text_update(self, mock_exists, mock_load):
        mock_exists.return_value = True
        mock_load.return_value = self.mock_wb

        self.mock_ws.iter_rows.return_value = [
            ("English",),
            ("Stage 1",),
            ("Reading",),
            ("1Rw.01 Know the name",),
        ]

        call_command("import_framework", "0058 English.xlsx")
        scheme = SchemeOfWork.objects.get(subject_code="0058")
        self.assertEqual(scheme.version, 1)

        # Run again with changed text
        self.mock_ws.iter_rows.return_value = [
            ("English",),
            ("Stage 1",),
            ("Reading - Updated",),
            ("1Rw.01 Know the name - UPDATED",),
        ]

        call_command("import_framework", "0058 English.xlsx")

        # Should still be version 1
        scheme = SchemeOfWork.objects.get(subject_code="0058")
        self.assertEqual(scheme.version, 1)

        lo = LearningObjective.objects.get(scheme=scheme, code="1Rw.01")
        self.assertEqual(lo.text, "Know the name - UPDATED")
        self.assertEqual(lo.topic.title, "Reading - Updated")

    @patch("apps.curriculum.management.commands.import_framework.openpyxl.load_workbook")
    @patch("apps.curriculum.management.commands.import_framework.Path.exists")
    def test_import_structural_change_bumps_version(self, mock_exists, mock_load):
        mock_exists.return_value = True
        mock_load.return_value = self.mock_wb

        self.mock_ws.iter_rows.return_value = [
            ("English",),
            ("Stage 1",),
            ("Reading",),
            ("1Rw.01 Know the name",),
        ]
        call_command("import_framework", "0058 English.xlsx")

        # Run again with added LO
        self.mock_ws.iter_rows.return_value = [
            ("English",),
            ("Stage 1",),
            ("Reading",),
            ("1Rw.01 Know the name",),
            ("1Rw.02 New objective",),
        ]
        call_command("import_framework", "0058 English.xlsx")

        # Should be version 2
        v2 = SchemeOfWork.objects.get(subject_code="0058", is_active=True)
        self.assertEqual(v2.version, 2)

        v1 = SchemeOfWork.objects.get(subject_code="0058", version=1)
        self.assertFalse(v1.is_active)
        self.assertEqual(v1.retired_on, date.today())

        self.assertEqual(LearningObjective.objects.filter(scheme=v2).count(), 2)
        self.assertEqual(LearningObjective.objects.filter(scheme=v1).count(), 1)
