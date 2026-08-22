from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.planning.models import PlanningTemplate, TemplateVersion
from apps.schools.models import School


@receiver(post_save, sender=School)
def seed_templates_for_new_school(sender, instance, created, **kwargs):
    if not created:
        return

    # Semester Work Plan Template
    swp_template, _ = PlanningTemplate.all_objects.get_or_create(
        school=instance,
        template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
        name="Universal Semester Work Plan",
        defaults={"is_active": True},
    )
    if not TemplateVersion.all_objects.filter(template=swp_template).exists():
        TemplateVersion.all_objects.create(
            school=instance,
            template=swp_template,
            version=1,
            status=TemplateVersion.Status.PUBLISHED,
            effective_from=timezone.localdate(),
            notes="Auto-provisioned Universal Semester Work Plan",
        )

    # Lesson Plan Template
    lp_template, _ = PlanningTemplate.all_objects.get_or_create(
        school=instance,
        template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
        name="Universal Lesson Plan",
        defaults={"is_active": True},
    )
    if not TemplateVersion.all_objects.filter(template=lp_template).exists():
        TemplateVersion.all_objects.create(
            school=instance,
            template=lp_template,
            version=1,
            status=TemplateVersion.Status.PUBLISHED,
            effective_from=timezone.localdate(),
            notes="Auto-provisioned Universal Lesson Plan",
        )
