from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.models import CurriculumFramework, LearningObjective, SchemeOfWork, Topic
from apps.planning.models import (
    LessonPlan,
    PlanningTemplate,
    TemplateField,
    TemplateVersion,
    WorkPlan,
)
from apps.planning.pdf import render_work_plan
from apps.planning.services import (
    create_lesson_plan,
    create_work_plan,
    save_lesson_plan,
    save_work_plan,
    transition_lesson_plan,
    transition_work_plan,
)
from apps.schools.models import (
    AcademicYear,
    CalendarWeek,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)

User = get_user_model()


class PlanningTemplateTests(TestCase):
    def setUp(self):
        self.alpha = School.objects.create(name="Alpha", slug="alpha", code="ALPHA")
        self.beta = School.objects.create(name="Beta", slug="beta", code="BETA")
        self.template = PlanningTemplate.all_objects.create(
            school=self.alpha,
            template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
            name="Lesson Plan",
        )
        self.version = TemplateVersion.all_objects.create(
            school=self.alpha, template=self.template, version=1
        )

    def test_tenant_manager_isolates_all_template_records(self):
        beta_template = PlanningTemplate.all_objects.create(
            school=self.beta,
            template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
            name="Lesson Plan",
        )
        with tenant_scope(self.alpha):
            self.assertEqual(list(PlanningTemplate.objects.all()), [self.template])
            self.assertEqual(list(TemplateVersion.objects.all()), [self.version])
        with tenant_scope(self.beta):
            self.assertEqual(list(PlanningTemplate.objects.all()), [beta_template])
            self.assertEqual(TemplateVersion.objects.count(), 0)

    def test_published_version_and_its_fields_cannot_change(self):
        field = TemplateField.all_objects.create(
            school=self.alpha,
            template_version=self.version,
            field_id="LP-D01",
            label="Date",
            field_class=TemplateField.FieldClass.BLUE,
            control_type=TemplateField.ControlType.DATE,
            sequence=1,
        )
        self.version.status = TemplateVersion.Status.PUBLISHED
        self.version.effective_from = date.today()
        self.version.save()

        field.label = "Lesson date"
        with self.assertRaises(ValidationError):
            field.save()
        with self.assertRaises(ValidationError):
            TemplateField.all_objects.create(
                school=self.alpha,
                template_version=self.version,
                field_id="LP-D02",
                label="Topic",
                field_class=TemplateField.FieldClass.BLUE,
                control_type=TemplateField.ControlType.SELECT,
                sequence=2,
            )

    def test_version_rejects_a_template_from_another_school(self):
        foreign_template = PlanningTemplate.all_objects.create(
            school=self.beta,
            template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
            name="Beta plan",
        )
        version = TemplateVersion(school=self.alpha, template=foreign_template, version=1)
        with self.assertRaises(ValidationError):
            version.full_clean()


class WorkPlanServiceTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Alpha", slug="alpha", code="ALPHA")
        self.teacher = User.objects.create_user(
            "teacher@example.com", "StrongPass!246", full_name="Teacher"
        )
        self.coordinator = User.objects.create_user(
            "coordinator@example.com", "StrongPass!246", full_name="Coordinator"
        )
        self.teacher_membership = Membership.objects.create(
            school=self.school, user=self.teacher, role=Membership.Role.TEACHER
        )
        self.coordinator_membership = Membership.objects.create(
            school=self.school, user=self.coordinator, role=Membership.Role.COORDINATOR
        )
        self.year = AcademicYear.all_objects.create(
            school=self.school,
            name="2026/2027",
            starts_on=date(2026, 8, 1),
            ends_on=date(2027, 7, 31),
        )
        self.term = Term.all_objects.create(
            school=self.school,
            academic_year=self.year,
            name="Semester 1",
            sequence=1,
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 12, 20),
        )
        self.week_one = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term,
            sequence=1,
            starts_on=date(2026, 8, 3),
            ends_on=date(2026, 8, 7),
            month_label="August",
        )
        self.event_week = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term,
            sequence=15,
            starts_on=date(2026, 11, 9),
            ends_on=date(2026, 11, 13),
            event_label="Revision Week",
            is_instructional=False,
        )
        subject = Subject.all_objects.create(
            school=self.school, name="Science", code="SCI", cambridge_code="SCI"
        )
        school_class = SchoolClass.all_objects.create(
            school=self.school, name="Year 8 Blue", year_group="Year 8"
        )
        self.assignment = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher,
            subject=subject,
            school_class=school_class,
            effective_from=date(2026, 1, 1),
        )
        framework = CurriculumFramework.objects.create(code="CLS", name="Cambridge Lower Secondary")
        self.scheme = SchemeOfWork.objects.create(
            framework=framework,
            subject_code="SCI",
            subject_name="Science",
            year_group="Year 8",
            title="Science Year 8",
        )
        self.topic = Topic.objects.create(scheme=self.scheme, title="Forces", sequence=1)
        self.objective = LearningObjective.objects.create(
            scheme=self.scheme, topic=self.topic, code="8Sc.01", text="Describe forces."
        )
        template = PlanningTemplate.all_objects.create(
            school=self.school,
            template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
            name="Semester Work Plan",
        )
        self.template_version = TemplateVersion.all_objects.create(
            school=self.school, template=template, version=1
        )
        self.template_version.status = TemplateVersion.Status.PUBLISHED
        self.template_version.effective_from = date(2026, 8, 1)
        self.template_version.save()

    def create_plan(self):
        with tenant_scope(self.school):
            return create_work_plan(
                school=self.school,
                author=self.teacher,
                assignment=self.assignment,
                academic_year=self.year,
                term=self.term,
                scheme=self.scheme,
            )

    def test_creation_snapshots_school_calendar_weeks_and_special_events(self):
        plan = self.create_plan()
        with tenant_scope(self.school):
            self.assertEqual(plan.weeks.count(), 2)
            special_week = plan.weeks.get(sequence=15)
        self.assertEqual(special_week.event_label, "Revision Week")
        self.assertFalse(special_week.is_instructional)

    def test_save_enforces_objective_topic_and_revision_scope(self):
        plan = self.create_plan()
        with tenant_scope(self.school):
            weeks = list(plan.weeks.order_by("sequence"))
        updates = [
            {
                "id": weeks[0].pk,
                "topic_id": self.topic.pk,
                "objectives": [self.objective.pk],
                "remarks": "Lab",
            },
            {"id": weeks[1].pk, "remarks": "Revision"},
        ]
        with tenant_scope(self.school):
            saved = save_work_plan(
                plan=plan, actor=self.teacher, revision=1, resources="Books", week_updates=updates
            )
        self.assertEqual(saved.revision, 2)
        with tenant_scope(self.school):
            self.assertEqual(weeks[0].objective_selections.count(), 1)
        with tenant_scope(self.school), self.assertRaises(ValidationError):
            save_work_plan(
                plan=saved, actor=self.teacher, revision=1, resources="Books", week_updates=updates
            )

    def test_workflow_is_audited_and_return_requires_feedback(self):
        plan = self.create_plan()
        with tenant_scope(self.school):
            transition_work_plan(
                plan=plan,
                actor_membership=self.teacher_membership,
                target_status=WorkPlan.Status.SUBMITTED,
            )
            with self.assertRaises(ValidationError):
                transition_work_plan(
                    plan=plan,
                    actor_membership=self.coordinator_membership,
                    target_status=WorkPlan.Status.RETURNED,
                )
            transition_work_plan(
                plan=plan,
                actor_membership=self.coordinator_membership,
                target_status=WorkPlan.Status.RETURNED,
                comment="Add learning objectives.",
            )
        plan.refresh_from_db()
        self.assertEqual(plan.status, WorkPlan.Status.RETURNED)
        with tenant_scope(self.school):
            self.assertEqual(plan.events.count(), 3)

    def test_renderer_emits_a_landscape_pdf_for_calendar_rows(self):
        plan = self.create_plan()
        output = BytesIO()
        with tenant_scope(self.school):
            render_work_plan(plan, output)
        self.assertTrue(output.getvalue().startswith(b"%PDF"))

    def test_lesson_plan_enforces_roster_bounds_and_submission_fields(self):
        template = PlanningTemplate.all_objects.create(
            school=self.school,
            template_type=PlanningTemplate.TemplateType.LESSON_PLAN,
            name="Lesson Plan",
        )
        version = TemplateVersion.all_objects.create(
            school=self.school, template=template, version=1
        )
        version.status = TemplateVersion.Status.PUBLISHED
        version.effective_from = date(2026, 8, 1)
        version.save()
        with tenant_scope(self.school):
            plan = create_lesson_plan(
                school=self.school,
                author=self.teacher,
                assignment=self.assignment,
                academic_year=self.year,
                term=self.term,
                scheme=self.scheme,
                lesson_date=date(2026, 8, 4),
                topic=self.topic,
            )
            with self.assertRaises(ValidationError):
                save_lesson_plan(
                    plan=plan,
                    actor=self.teacher,
                    revision=1,
                    values={
                        "boys_attendance": 1,
                        "girls_attendance": 0,
                        "main_teaching_activity": "Discuss forces.",
                        "assessment_ideas": "Questioning.",
                    },
                    objective_ids=[self.objective.pk],
                )
            plan = save_lesson_plan(
                plan=plan,
                actor=self.teacher,
                revision=1,
                values={
                    "boys_attendance": 0,
                    "girls_attendance": 0,
                    "main_teaching_activity": "Discuss forces.",
                    "assessment_ideas": "Questioning.",
                },
                objective_ids=[self.objective.pk],
            )
            transition_lesson_plan(
                plan=plan,
                actor_membership=self.teacher_membership,
                target_status=LessonPlan.Status.SUBMITTED,
            )
        self.assertEqual(plan.status, LessonPlan.Status.SUBMITTED)
