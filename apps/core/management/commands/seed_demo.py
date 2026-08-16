from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.core.tenant import tenant_scope
from apps.curriculum.models import LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.planning import services as template_services
from apps.planning.models import PlanType, TemplateVersion
from apps.plans import services as plan_services
from apps.plans import workflow
from apps.schools.models import (
    AcademicYear,
    AuditLog,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Create a local demonstration school, leader, teacher and assignment."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="DemoPass!246")

    def handle(self, *args, **options):
        password = options["password"]
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
        coordinator, _ = User.objects.get_or_create(
            email="coordinator@demo.cambrify.local",
            defaults={"full_name": "Cora Coordinator"},
        )
        coordinator.full_name = "Cora Coordinator"
        coordinator.set_password(password)
        coordinator.save()
        head, _ = User.objects.get_or_create(
            email="head@demo.cambrify.local", defaults={"full_name": "Hana Head"}
        )
        head.full_name = "Hana Head"
        head.set_password(password)
        head.save()
        Membership.objects.update_or_create(
            school=school,
            user=coordinator,
            defaults={"role": Membership.Role.COORDINATOR, "status": "active"},
        )
        head_membership, _ = Membership.objects.update_or_create(
            school=school,
            user=head,
            defaults={"role": Membership.Role.HEAD, "status": "active"},
        )
        year, _ = AcademicYear.all_objects.get_or_create(
            school=school,
            name="2026/2027",
            defaults={
                "starts_on": date(2026, 8, 1),
                "ends_on": date(2027, 7, 15),
                "is_current": True,
            },
        )
        term, _ = Term.all_objects.get_or_create(
            school=school,
            academic_year=year,
            sequence=1,
            defaults={
                "name": "Semester 1",
                "starts_on": date(2026, 8, 3),
                "ends_on": date(2026, 12, 18),
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
        assignment, _ = TeacherAssignment.all_objects.get_or_create(
            school=school,
            teacher=teacher,
            subject=subject,
            school_class=school_class,
            effective_from=date(2026, 8, 1),
        )
        teacher_membership = Membership.objects.get(school=school, user=teacher)

        with tenant_scope(school.pk):
            self._seed_templates(school, head_membership)
            scheme = self._seed_curriculum(school, subject, school_class, year, term)
            self._seed_plans(teacher_membership, head_membership, assignment, term, scheme)
        AuditLog.all_objects.get_or_create(
            school=school,
            actor=director,
            action="demo.seeded",
            target_type="school",
            target_id=str(school.pk),
        )
        self.stdout.write(self.style.SUCCESS("Demo school ready"))
        for label, email in (
            ("Director", "director@demo.cambrify.local"),
            ("Head", "head@demo.cambrify.local"),
            ("Coordinator", "coordinator@demo.cambrify.local"),
            ("Teacher", "teacher@demo.cambrify.local"),
        ):
            self.stdout.write(f"{label:12} {email} / {password}")

    def _seed_templates(self, school, approver):
        """Publish TemplateVersion 1 for both plan types.

        The demo approves a placeholder clean master so the builders and PDF
        renderer are usable. A real deployment must attach the school's own
        unmarked master before this step.
        """
        for plan_type in (PlanType.LESSON_PLAN, PlanType.WORK_PLAN):
            if TemplateVersion.all_objects.filter(
                school=school, template__plan_type=plan_type
            ).exists():
                continue
            version = template_services.create_draft_version(
                membership=approver, plan_type=plan_type
            )
            template_services.record_clean_master(
                membership=approver,
                version=version,
                filename=f"demo-{plan_type}-clean-master.pdf",
                checksum=f"{plan_type:0<64}"[:64].replace("_", "0"),
                approved=True,
            )
            template_services.approve_version(membership=approver, version=version)
            template_services.publish_version(membership=approver, version=version)

    def _seed_curriculum(self, school, subject, school_class, year, term):
        scheme, _ = SchemeOfWork.all_objects.get_or_create(
            school=school,
            code="SCI-Y8-S1",
            version=1,
            defaults={
                "subject": subject,
                "school_class": school_class,
                "academic_year": year,
                "term": term,
                "title": "Science Year 8 Semester 1",
                "status": SchemeOfWork.Status.PUBLISHED,
            },
        )
        content = [
            (
                "T1",
                "Forces and motion",
                "T1.1",
                "Speed",
                [
                    ("8Ps.01", "Calculate average speed from distance and time."),
                    ("8Ps.02", "Interpret distance-time graphs for uniform motion."),
                ],
            ),
            (
                "T2",
                "Energy",
                "T2.1",
                "Energy stores",
                [
                    ("8Pe.01", "Identify energy stores and describe transfers between them."),
                    ("8Pe.02", "Explain conservation of energy in a closed system."),
                ],
            ),
            (
                "T3",
                "Matter",
                "T3.1",
                "Particle model",
                [
                    (
                        "8Cm.01",
                        "Describe the arrangement of particles in solids, liquids and gases.",
                    ),
                ],
            ),
        ]
        for index, (code, title, sub_code, sub_title, objectives) in enumerate(content, start=1):
            topic, _ = Topic.all_objects.get_or_create(
                school=school,
                scheme=scheme,
                code=code,
                defaults={"title": title, "sequence": index},
            )
            subtopic, _ = Subtopic.all_objects.get_or_create(
                school=school,
                topic=topic,
                code=sub_code,
                defaults={"title": sub_title, "sequence": 1},
            )
            for order, (lo_code, text) in enumerate(objectives, start=1):
                LearningObjective.all_objects.get_or_create(
                    school=school,
                    topic=topic,
                    code=lo_code,
                    defaults={"subtopic": subtopic, "text": text, "sequence": order},
                )
        return scheme

    def _seed_plans(self, teacher, head, assignment, term, scheme):
        """Create a worked example in each interesting workflow state."""
        from apps.plans.models import LessonPlan, WorkPlan

        if not WorkPlan.all_objects.filter(assignment=assignment, term=term).exists():
            work_plan = plan_services.create_work_plan(
                membership=teacher, assignment_id=assignment.pk, term=term, scheme=scheme
            )
            objectives = list(
                LearningObjective.objects.filter(topic__scheme=scheme).order_by("code")
            )
            rows = list(plan_services.week_rows_for(work_plan))
            for index, row in enumerate(rows):
                if row.event_label or index >= 8:
                    continue
                chosen = objectives[index % len(objectives)]
                plan_services.save_work_plan_row(
                    membership=teacher,
                    plan=work_plan,
                    row=row,
                    objective_ids=[chosen.pk],
                    remarks="Practical work and a short end-of-week check.",
                )
            plan_services.save_work_plan_resources(
                membership=teacher,
                plan=work_plan,
                resources=(
                    "Lesson Notes, Projector, Laptop, Learner's Book, "
                    "Teacher's Resource, Whiteboard/marker"
                ),
            )

        if LessonPlan.all_objects.filter(assignment=assignment).exists():
            return

        subtopic = Subtopic.objects.filter(topic__scheme=scheme).order_by("code").first()
        objective_ids = list(
            LearningObjective.objects.filter(subtopic=subtopic).values_list("pk", flat=True)
        )
        examples = [
            (date(2026, 9, 15), "approved", 12, 13),
            (date(2026, 9, 22), "submitted", 11, 14),
            (date(2026, 9, 29), "draft", None, None),
        ]
        for lesson_date, target_state, boys, girls in examples:
            plan = plan_services.create_lesson_plan(
                membership=teacher, assignment_id=assignment.pk, lesson_date=lesson_date
            )
            plan_services.save_lesson_plan(
                membership=teacher,
                plan=plan,
                subtopic_id=subtopic.pk,
                objective_ids=objective_ids,
                boys_present=boys if boys is not None else ...,
                girls_present=girls if girls is not None else ...,
                main_teaching_activity=(
                    "Starter: recall distance and time units.\n"
                    "Main: learners time a trolley over 1m, 2m and 3m, record results "
                    "in a table and calculate average speed.\n"
                    "Plenary: compare results and discuss sources of error."
                ),
                assessment_ideas=(
                    "Exit ticket calculating average speed for two worked examples. "
                    "Circulate during the practical and question pairs on unit conversion."
                ),
                notes_remarks="Prepare eight trolleys and stopwatches before the lesson.",
            )
            if target_state in {"submitted", "approved"}:
                workflow.submit(membership=teacher, plan=plan)
            if target_state == "approved":
                workflow.approve(
                    membership=head, plan=plan, comment="Clear objectives and good assessment."
                )
