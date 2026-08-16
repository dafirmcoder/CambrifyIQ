"""Import a curriculum CSV into a scheme of work, printing a quality report."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.tenant import tenant_scope
from apps.curriculum.importers import import_curriculum, parse_csv
from apps.curriculum.models import SchemeOfWork
from apps.schools.models import Membership, School


class Command(BaseCommand):
    help = "Import topics, sub-topics and learning objectives from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("school_code", help="School code, for example LIS.")
        parser.add_argument("scheme_code", help="Scheme code, for example LIS-SCI-Y8-S1.")
        parser.add_argument("csv_path", help="Path to the curriculum CSV file.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report without saving any rows.",
        )

    def handle(self, *args, **options):
        school = School.objects.filter(code=options["school_code"]).first()
        if school is None:
            raise CommandError(f"No school with code {options['school_code']}.")

        path = Path(options["csv_path"])
        if not path.exists():
            raise CommandError(f"No file at {path}.")

        membership = Membership.objects.filter(
            school=school,
            role__in=(Membership.Role.COORDINATOR, Membership.Role.HEAD),
            status=Membership.Status.ACTIVE,
        ).first()
        if membership is None:
            raise CommandError("The school needs an active Coordinator or Head to import.")

        with tenant_scope(school.pk):
            scheme = SchemeOfWork.objects.filter(code=options["scheme_code"]).first()
            if scheme is None:
                raise CommandError(f"No scheme with code {options['scheme_code']}.")

            report = import_curriculum(
                membership=membership,
                scheme=scheme,
                rows=parse_csv(path.read_text(encoding="utf-8")),
                dry_run=options["dry_run"],
            )

        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))
        for error in report.errors:
            self.stdout.write(self.style.ERROR(f"error: {error}"))

        if not report.ok:
            raise CommandError(f"Import aborted with {len(report.errors)} error(s).")

        prefix = "Dry run" if options["dry_run"] else "Imported"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: {report.rows_read} rows read, "
                f"{report.topics_created} topics, "
                f"{report.subtopics_created} sub-topics and "
                f"{report.objectives_created} objectives created."
            )
        )
