from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.planning.models import PlanningTemplate, TemplateVersion
from apps.schools.models import School


class Command(BaseCommand):
    help = "Seeds the universal PlanningTemplate and TemplateVersion for all existing schools."

    def handle(self, *args, **options):
        schools = School.objects.all()
        created_count = 0

        with transaction.atomic():
            for school in schools:
                # Semester Work Plan
                swp_template, created_swp = PlanningTemplate.all_objects.get_or_create(
                    school=school,
                    template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
                    name="Universal Semester Work Plan",
                    defaults={"is_active": True},
                )
                if not TemplateVersion.all_objects.filter(template=swp_template).exists():
                    TemplateVersion.all_objects.create(
                        school=school,
                        template=swp_template,
                        version=1,
                        status=TemplateVersion.Status.PUBLISHED,
                        effective_from=timezone.localdate(),
                        notes="Auto-provisioned Universal Semester Work Plan",
                    )
                    created_count += 1

                # Lesson Plan
                lp_template, created_lp = PlanningTemplate.all_objects.get_or_create(
                    school=school,
                    template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
                    name="Universal Lesson Plan",
                    defaults={"is_active": True},
                )
                if not TemplateVersion.all_objects.filter(template=lp_template).exists():
                    TemplateVersion.all_objects.create(
                        school=school,
                        template=lp_template,
                        version=1,
                        status=TemplateVersion.Status.PUBLISHED,
                        effective_from=timezone.localdate(),
                        notes="Auto-provisioned Universal Lesson Plan",
                    )
                    created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully provisioned {created_count} universal template versions."
            )
        )
