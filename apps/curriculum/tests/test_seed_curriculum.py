import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)
from apps.curriculum.services.extractor import CurriculumExtractor, parse_lo_line


class CurriculumExtractorUnitTests(TestCase):
    def test_parse_lo_line(self):
        # Valid standard codes
        self.assertEqual(
            parse_lo_line("7Bs.01 Identify the structures present in plant cells."),
            ("7Bs.01", "Identify the structures present in plant cells."),
        )
        self.assertEqual(
            parse_lo_line("123PPco.01 Describe what makes up a community."),
            ("123PPco.01", "Describe what makes up a community."),
        )
        self.assertEqual(
            parse_lo_line("E.01 Encounter, sense, experiment with art."),
            ("E.01", "Encounter, sense, experiment with art."),
        )
        # Non-LO lines
        self.assertIsNone(parse_lo_line("Biology"))
        self.assertIsNone(parse_lo_line("Learning Objectives"))
        self.assertIsNone(parse_lo_line("Sub-strands"))
        self.assertIsNone(parse_lo_line(""))


class SeedCurriculumCommandTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

        sample_fixture = {
            "code": "CAMBRIDGE_LOWER_SECONDARY",
            "name": "Cambridge Lower Secondary",
            "publisher": "Cambridge International",
            "schemes": [
                {
                    "subject_code": "0893",
                    "subject_name": "Science",
                    "framework": "Cambridge Lower Secondary",
                    "year_group": "Stage 7",
                    "title": "Science Stage 7",
                    "topics": [
                        {
                            "title": "Biology",
                            "los": [
                                {
                                    "code": "7Bs.01",
                                    "text": "Identify structures present in plant and animal cells.",
                                    "subtopic": "Structure and function",
                                },
                                {
                                    "code": "7Bs.02",
                                    "text": "Describe the functions of cell components.",
                                    "subtopic": "Structure and function",
                                },
                            ],
                        },
                        {
                            "title": "Chemistry",
                            "los": [
                                {
                                    "code": "7Cs.01",
                                    "text": "Understand that all matter is made of atoms.",
                                    "subtopic": "States of matter",
                                }
                            ],
                        },
                    ],
                }
            ],
        }

        with open(self.data_dir / "test_fixture.json", "w", encoding="utf-8") as f:
            json.dump(sample_fixture, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_seed_curriculum_creates_hierarchy(self):
        call_command("seed_curriculum", data_dir=str(self.data_dir))

        self.assertEqual(CurriculumFramework.objects.count(), 1)
        self.assertEqual(SchemeOfWork.objects.count(), 1)
        self.assertEqual(Topic.objects.count(), 2)
        self.assertEqual(Subtopic.objects.count(), 2)
        self.assertEqual(LearningObjective.objects.count(), 3)

        scheme = SchemeOfWork.objects.get(subject_code="0893", year_group="Stage 7")
        self.assertEqual(scheme.title, "Science Stage 7")

        bio_topic = Topic.objects.get(scheme=scheme, title="Biology")
        self.assertEqual(bio_topic.learning_objectives.count(), 2)

        lo1 = LearningObjective.objects.get(code="7Bs.01")
        self.assertEqual(lo1.topic, bio_topic)
        self.assertEqual(lo1.subtopic.title, "Structure and function")

    def test_seed_curriculum_is_idempotent(self):
        call_command("seed_curriculum", data_dir=str(self.data_dir))
        # Second run
        call_command("seed_curriculum", data_dir=str(self.data_dir))

        self.assertEqual(CurriculumFramework.objects.count(), 1)
        self.assertEqual(SchemeOfWork.objects.count(), 1)
        self.assertEqual(Topic.objects.count(), 2)
        self.assertEqual(Subtopic.objects.count(), 2)
        self.assertEqual(LearningObjective.objects.count(), 3)

    def test_seed_curriculum_dry_run(self):
        call_command("seed_curriculum", data_dir=str(self.data_dir), dry_run=True)

        self.assertEqual(CurriculumFramework.objects.count(), 0)
        self.assertEqual(SchemeOfWork.objects.count(), 0)
        self.assertEqual(Topic.objects.count(), 0)
        self.assertEqual(LearningObjective.objects.count(), 0)
