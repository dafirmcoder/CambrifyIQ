from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)

User = get_user_model()


class CurriculumFilteringAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="teacher@school.org",
            password="StrongPassword123!",
            first_name="Test",
            last_name="Teacher",
        )

        # Primary framework & scheme
        self.primary_fw = CurriculumFramework.objects.create(
            code=CurriculumFramework.FrameworkCode.PRIMARY,
            name="Cambridge Primary",
            publisher="Cambridge",
        )
        self.primary_scheme = SchemeOfWork.objects.create(
            framework=self.primary_fw,
            subject_code="0058",
            subject_name="English",
            year_group="Stage 1",
            title="Stage 1 English",
            version=1,
        )
        self.p_topic = Topic.objects.create(
            scheme=self.primary_scheme,
            title="Reading",
            sequence=1,
            code="1Rw",
        )
        self.p_subtopic = Subtopic.objects.create(
            topic=self.p_topic,
            title="Word Recognition",
            sequence=1,
            code="1Rw.01",
        )
        self.p_lo = LearningObjective.objects.create(
            scheme=self.primary_scheme,
            topic=self.p_topic,
            subtopic=self.p_subtopic,
            code="1Rw.01",
            text="Recognise, say and write the common spellings.",
            sequence=1,
        )

        # Lower Secondary framework & scheme
        self.lower_fw = CurriculumFramework.objects.create(
            code=CurriculumFramework.FrameworkCode.LOWER_SECONDARY,
            name="Cambridge Lower Secondary",
            publisher="Cambridge",
        )
        self.lower_scheme = SchemeOfWork.objects.create(
            framework=self.lower_fw,
            subject_code="0893",
            subject_name="Science",
            year_group="Stage 7",
            title="Stage 7 Science",
            version=1,
        )
        self.ls_topic = Topic.objects.create(
            scheme=self.lower_scheme,
            title="Biology: Cells",
            sequence=1,
            code="7Bs",
        )
        self.ls_lo = LearningObjective.objects.create(
            scheme=self.lower_scheme,
            topic=self.ls_topic,
            code="7Bs.01",
            text="Identify and describe the structure of plant and animal cells.",
            sequence=1,
        )

        # IGCSE framework & scheme
        self.igcse_fw = CurriculumFramework.objects.create(
            code=CurriculumFramework.FrameworkCode.IGCSE,
            name="Cambridge IGCSE",
            publisher="Cambridge",
        )
        self.igcse_scheme = SchemeOfWork.objects.create(
            framework=self.igcse_fw,
            subject_code="0580",
            subject_name="Mathematics",
            year_group="Years 10-11 (IGCSE)",
            title="IGCSE Mathematics",
            version=1,
        )
        self.igcse_topic = Topic.objects.create(
            scheme=self.igcse_scheme,
            title="Number: Types of Number",
            sequence=1,
            code="C1.1",
        )
        self.igcse_lo = LearningObjective.objects.create(
            scheme=self.igcse_scheme,
            topic=self.igcse_topic,
            code="0580.C1.1.1",
            text="Identify and use natural numbers, integers, prime numbers.",
            sequence=1,
        )

        # AS & A Level framework
        self.alevel_fw = CurriculumFramework.objects.create(
            code=CurriculumFramework.FrameworkCode.AS_A_LEVEL,
            name="Cambridge International AS & A Level",
            publisher="Cambridge",
        )
        self.alevel_scheme = SchemeOfWork.objects.create(
            framework=self.alevel_fw,
            subject_code="9709",
            subject_name="Mathematics",
            year_group="AS & A Level",
            title="AS & A Level Mathematics",
            version=1,
        )

    def test_frameworks_api_returns_all_frameworks(self):
        response = self.client.get(reverse("curriculum:api_frameworks"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("frameworks", data)
        codes = [fw["code"] for fw in data["frameworks"]]
        self.assertIn(CurriculumFramework.FrameworkCode.PRIMARY, codes)
        self.assertIn(CurriculumFramework.FrameworkCode.LOWER_SECONDARY, codes)
        self.assertIn(CurriculumFramework.FrameworkCode.IGCSE, codes)
        self.assertIn(CurriculumFramework.FrameworkCode.AS_A_LEVEL, codes)

    def test_filter_schemes_by_framework(self):
        # Filter for IGCSE
        response = self.client.get(
            reverse("curriculum:api_schemes"),
            {"framework": CurriculumFramework.FrameworkCode.IGCSE},
        )
        self.assertEqual(response.status_code, 200)
        schemes = response.json()["schemes"]
        self.assertEqual(len(schemes), 1)
        self.assertEqual(schemes[0]["subject_code"], "0580")

        # Filter for Primary
        response = self.client.get(
            reverse("curriculum:api_schemes"),
            {"framework": CurriculumFramework.FrameworkCode.PRIMARY},
        )
        schemes = response.json()["schemes"]
        self.assertEqual(len(schemes), 1)
        self.assertEqual(schemes[0]["subject_code"], "0058")

        # Filter for Lower Secondary
        response = self.client.get(
            reverse("curriculum:api_schemes"),
            {"framework": CurriculumFramework.FrameworkCode.LOWER_SECONDARY},
        )
        schemes = response.json()["schemes"]
        self.assertEqual(len(schemes), 1)
        self.assertEqual(schemes[0]["subject_code"], "0893")

    def test_filter_schemes_by_subject_and_year_group(self):
        response = self.client.get(
            reverse("curriculum:api_schemes"),
            {"subject_code": "0580", "year_group": "Years 10-11 (IGCSE)"},
        )
        self.assertEqual(response.status_code, 200)
        schemes = response.json()["schemes"]
        self.assertEqual(len(schemes), 1)
        self.assertEqual(schemes[0]["title"], "IGCSE Mathematics")

    def test_filter_topics_by_scheme(self):
        response = self.client.get(
            reverse("curriculum:api_topics"),
            {"scheme": str(self.primary_scheme.pk)},
        )
        self.assertEqual(response.status_code, 200)
        topics = response.json()["topics"]
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["title"], "Reading")

    def test_filter_subtopics_by_topic(self):
        response = self.client.get(
            reverse("curriculum:api_subtopics"),
            {"topic": str(self.p_topic.pk)},
        )
        self.assertEqual(response.status_code, 200)
        subtopics = response.json()["subtopics"]
        self.assertEqual(len(subtopics), 1)
        self.assertEqual(subtopics[0]["title"], "Word Recognition")

    def test_filter_objectives_by_scheme_topic_and_search(self):
        # By scheme
        response = self.client.get(
            reverse("curriculum:api_objectives"),
            {"scheme": str(self.igcse_scheme.pk)},
        )
        self.assertEqual(response.status_code, 200)
        objs = response.json()["objectives"]
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["code"], "0580.C1.1.1")

        # By search keyword
        response = self.client.get(
            reverse("curriculum:api_objectives"),
            {"q": "plant and animal cells"},
        )
        self.assertEqual(response.status_code, 200)
        objs = response.json()["objectives"]
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["code"], "7Bs.01")

    def test_curriculum_browser_page_renders_for_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("curriculum:browser"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Curriculum Objectives")
        self.assertContains(response, "Cambridge Primary")
        self.assertContains(response, "Cambridge IGCSE")
