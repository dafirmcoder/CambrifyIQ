"""Seed TemplateVersion 1 drafts from the verified annotation register."""

from django.core.management.base import BaseCommand, CommandError

from apps.core.tenant import tenant_scope
from apps.planning import services
from apps.planning.models import PlanType, TemplateVersion
from apps.planning.register import counts_by_kind
from apps.schools.models import Membership, School


class Command(BaseCommand):
    help = "Create draft Lesson Plan and Work Plan template versions for a school."

    def add_arguments(self, parser):
        parser.add_argument("school_code", help="School code, for example LIS.")
        parser.add_argument(
            "--type",
            choices=PlanType.values,
            action="append",
            dest="plan_types",
            help="Limit to one plan type. Repeatable. Defaults to both.",
        )

    def handle(self, *args, **options):
        school = School.objects.filter(code=options["school_code"]).first()
        if school is None:
            raise CommandError(f"No school with code {options['school_code']}.")

        membership = (
            Membership.objects.filter(
                school=school,
                role__in=(Membership.Role.HEAD, Membership.Role.DIRECTOR),
                status=Membership.Status.ACTIVE,
            )
            .order_by("role")
            .first()
        )
        if membership is None:
            raise CommandError("The school needs an active Head or Director first.")

        plan_types = options.get("plan_types") or list(PlanType.values)
        with tenant_scope(school.pk):
            for plan_type in plan_types:
                existing = TemplateVersion.all_objects.filter(
                    school=school, template__plan_type=plan_type
                ).exists()
                if existing:
                    self.stdout.write(
                        self.style.WARNING(f"{plan_type}: versions already exist, skipping.")
                    )
                    continue

                version = services.create_draft_version(membership=membership, plan_type=plan_type)
                totals = counts_by_kind(plan_type)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{plan_type}: draft v{version.version} created with "
                        f"{totals['red']} RED, {totals['blue']} BLUE and "
                        f"{totals['system']} system fields."
                    )
                )
                for blocker in services.validate_for_lock(version):
                    self.stdout.write(self.style.WARNING(f"  pending: {blocker}"))
