from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.schools.models import (
    AcademicYear,
    AuditLog,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Create a local demonstration school, leader, teacher and assignment."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="DemoPass!246")
        parser.add_argument(
            "--skip-curriculum",
            action="store_true",
            help="Skip seeding Cambridge curriculum learning objectives",
        )

    def handle(self, *args, **options):
        password = options["password"]
        skip_curriculum = options["skip_curriculum"]

        if not skip_curriculum:
            from django.core.management import call_command
            call_command("seed_curriculum")
        director, _ = User.objects.get_or_create(
            email="director@demo.cambrify.local",
            defaults={"full_name": "Amina Director"},
        )
        director.full_name = "Amina Director"
        director.set_password(password)
        director.save()
        teacher, _ = User.objects.get_or_create(
            email="teacher@demo.cambrify.local",
            defaults={"full_name": "Daniel Teacher"},
        )
        teacher.full_name = "Daniel Teacher"
        teacher.set_password(password)
        teacher.save()

        school, _ = School.objects.get_or_create(
            slug="bahari-cambridge-demo",
            defaults={
                "name": "Bahari Cambridge School",
                "code": "BAHARI-DEMO",
                "address": "Dar es Salaam, Tanzania",
                "country": "TZ",
                "created_by": director,
                "onboarding_complete": True,
            },
        )
        Membership.objects.update_or_create(
            school=school,
            user=director,
            defaults={"role": Membership.Role.DIRECTOR, "is_primary": True, "status": "active"},
        )
        Membership.objects.update_or_create(
            school=school,
            user=teacher,
            defaults={"role": Membership.Role.TEACHER, "status": "active"},
        )
        AcademicYear.all_objects.get_or_create(
            school=school,
            name="2026/2027",
            defaults={
                "starts_on": date(2026, 8, 1),
                "ends_on": date(2027, 7, 15),
                "is_current": True,
            },
        )
        subject, _ = Subject.all_objects.get_or_create(
            school=school,
            code="SCI",
            defaults={"name": "Science", "cambridge_code": "0893"},
        )
        school_class, _ = SchoolClass.all_objects.get_or_create(
            school=school,
            name="Year 8",
            defaults={"year_group": "Year 8", "boys_count": 12, "girls_count": 14},
        )
        TeacherAssignment.all_objects.get_or_create(
            school=school,
            teacher=teacher,
            subject=subject,
            school_class=school_class,
            effective_from=date(2026, 8, 1),
        )
        AuditLog.all_objects.get_or_create(
            school=school,
            actor=director,
            action="demo.seeded",
            target_type="school",
            target_id=str(school.pk),
        )
        self.stdout.write(self.style.SUCCESS("Demo school ready"))
        self.stdout.write(f"Director: director@demo.cambrify.local / {password}")
        self.stdout.write(f"Teacher:  teacher@demo.cambrify.local / {password}")
