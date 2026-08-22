from django.core.management import call_command
from django.test import TestCase

from apps.planning.models import PlanningTemplate, TemplateVersion
from apps.schools.models import School


class UniversalTemplateTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Template School")
        # School creation triggers the signal.

    def test_signal_provisions_templates(self):
        swp = PlanningTemplate.all_objects.get(
            school=self.school, template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN
        )
        lp = PlanningTemplate.all_objects.get(
            school=self.school, template_type=PlanningTemplate.TemplateType.LESSON_PLAN
        )

        self.assertEqual(swp.name, "Universal Semester Work Plan")
        self.assertEqual(lp.name, "Universal Lesson Plan")

        swp_v = TemplateVersion.all_objects.get(template=swp)
        self.assertEqual(swp_v.status, TemplateVersion.Status.PUBLISHED)

        lp_v = TemplateVersion.all_objects.get(template=lp)
        self.assertEqual(lp_v.status, TemplateVersion.Status.PUBLISHED)

    def test_management_command_idempotency(self):
        # Already created by signal, running command should not duplicate
        call_command("seed_universal_templates")

        self.assertEqual(PlanningTemplate.all_objects.filter(school=self.school).count(), 2)
        self.assertEqual(TemplateVersion.all_objects.filter(school=self.school).count(), 2)

        # If we delete the versions and run again, it should backfill them
        TemplateVersion.all_objects.all().delete()
        self.assertEqual(TemplateVersion.all_objects.filter(school=self.school).count(), 0)

        call_command("seed_universal_templates")
        self.assertEqual(TemplateVersion.all_objects.filter(school=self.school).count(), 2)
