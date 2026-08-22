from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Topic,
)
from apps.schools.models import School


class GlobalCurriculumTests(TestCase):
    def setUp(self):
        self.framework = CurriculumFramework.objects.create(
            code="CLS", name="Cambridge Lower Secondary"
        )
        self.scheme = SchemeOfWork.objects.create(
            framework=self.framework,
            subject_code="1113",
            subject_name="Science",
            year_group="Year 8",
            title="Science Year 8",
            version=1,
        )

    def test_curriculum_is_visible_independently_of_active_school(self):
        alpha = School.objects.create(name="Alpha", slug="alpha", code="ALPHA")
        beta = School.objects.create(name="Beta", slug="beta", code="BETA")

        with tenant_scope(alpha):
            self.assertEqual(SchemeOfWork.objects.get().pk, self.scheme.pk)
        with tenant_scope(beta):
            self.assertEqual(SchemeOfWork.objects.get().pk, self.scheme.pk)

    def test_learning_objective_rejects_a_topic_from_another_scheme(self):
        other_scheme = SchemeOfWork.objects.create(
            framework=self.framework,
            subject_code="1120",
            subject_name="Mathematics",
            year_group="Year 8",
            title="Mathematics Year 8",
            version=1,
        )
        foreign_topic = Topic.objects.create(scheme=other_scheme, title="Number", sequence=1)
        objective = LearningObjective(
            scheme=self.scheme, topic=foreign_topic, code="8Sc.01", text="Investigate."
        )
        with self.assertRaises(ValidationError):
            objective.full_clean()
