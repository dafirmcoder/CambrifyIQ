from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)
from apps.planning.forms import WorkPlanCreateForm
from apps.planning.models import (
    PlanningTemplate,
    TemplateVersion,
    WorkPlanWeek,
    WorkPlanWeekObjective,
)
from apps.planning.pdf import render_work_plan
from apps.planning.services import create_work_plan, save_work_plan
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


class WorkPlanFlowTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(
            name="Cambridge Academy", slug="cambridge-academy", code="CAM01"
        )
        self.other_school = School.objects.create(
            name="Other Academy", slug="other-academy", code="OTH01"
        )

        self.teacher = User.objects.create_user(
            "teacher1@example.com", "Password!123", full_name="Jane Doe"
        )
        self.other_teacher = User.objects.create_user(
            "teacher2@example.com", "Password!123", full_name="John Smith"
        )

        self.membership = Membership.objects.create(
            school=self.school, user=self.teacher, role=Membership.Role.TEACHER
        )
        self.other_membership = Membership.objects.create(
            school=self.other_school, user=self.other_teacher, role=Membership.Role.TEACHER
        )

        self.academic_year = AcademicYear.all_objects.create(
            school=self.school,
            name="2026/2027",
            starts_on=date(2026, 8, 1),
            ends_on=date(2027, 7, 31),
        )
        self.term = Term.all_objects.create(
            school=self.school,
            academic_year=self.academic_year,
            name="Term 1",
            sequence=1,
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 12, 15),
        )

        self.week1 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term,
            sequence=1,
            starts_on=date(2026, 8, 3),
            ends_on=date(2026, 8, 7),
            month_label="August",
            is_instructional=True,
        )
        self.week2 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term,
            sequence=2,
            starts_on=date(2026, 8, 10),
            ends_on=date(2026, 8, 14),
            month_label="August",
            is_instructional=True,
        )
        self.break_week = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term,
            sequence=8,
            starts_on=date(2026, 9, 21),
            ends_on=date(2026, 9, 25),
            event_label="Mid-term Break",
            is_instructional=False,
        )

        self.subject = Subject.all_objects.create(
            school=self.school, name="Mathematics", code="MAT", cambridge_code="0845"
        )
        self.science_subject = Subject.all_objects.create(
            school=self.school, name="Science", code="SCI", cambridge_code="0846"
        )
        self.class_7a = SchoolClass.all_objects.create(
            school=self.school, name="Year 7A", year_group="Stage 7"
        )
        self.class_8a = SchoolClass.all_objects.create(
            school=self.school, name="Year 8A", year_group="Stage 8"
        )

        self.assignment = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher,
            subject=self.subject,
            school_class=self.class_7a,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        self.framework = CurriculumFramework.objects.create(
            code="CLS", name="Cambridge Lower Secondary"
        )
        self.scheme = SchemeOfWork.objects.create(
            framework=self.framework,
            subject_code="0845",
            subject_name="Mathematics",
            year_group="Stage 7",
            title="Mathematics Stage 7",
            is_active=True,
        )
        self.science_scheme = SchemeOfWork.objects.create(
            framework=self.framework,
            subject_code="0846",
            subject_name="Science",
            year_group="Stage 7",
            title="Science Stage 7",
            is_active=True,
        )

        self.topic1 = Topic.objects.create(
            scheme=self.scheme, title="Number and Calculation", sequence=1
        )
        self.unit1 = Subtopic.objects.create(
            topic=self.topic1, title="Integers and Powers", sequence=1
        )
        self.unit2 = Subtopic.objects.create(
            topic=self.topic1, title="Decimals and Fractions", sequence=2
        )

        self.topic2 = Topic.objects.create(scheme=self.scheme, title="Algebra", sequence=2)
        self.unit3 = Subtopic.objects.create(
            topic=self.topic2, title="Expressions and Formulae", sequence=1
        )

        self.obj_unit1 = LearningObjective.objects.create(
            scheme=self.scheme,
            topic=self.topic1,
            subtopic=self.unit1,
            code="7Nc.01",
            text="Understand powers.",
        )
        self.obj_unit2 = LearningObjective.objects.create(
            scheme=self.scheme,
            topic=self.topic1,
            subtopic=self.unit2,
            code="7Nc.02",
            text="Add fractions.",
        )
        self.obj_topic2 = LearningObjective.objects.create(
            scheme=self.scheme,
            topic=self.topic2,
            subtopic=self.unit3,
            code="7Ae.01",
            text="Simplify expressions.",
        )
        self.obj_general = LearningObjective.objects.create(
            scheme=self.scheme,
            topic=self.topic1,
            subtopic=None,
            code="7Nc.00",
            text="General number sense.",
        )

        # Foreign scheme objective
        self.foreign_topic = Topic.objects.create(
            scheme=self.science_scheme, title="Forces", sequence=1
        )
        self.foreign_obj = LearningObjective.objects.create(
            scheme=self.science_scheme,
            topic=self.foreign_topic,
            code="7Sc.01",
            text="Describe forces.",
        )

        # Template
        template = PlanningTemplate.all_objects.create(
            school=self.school,
            template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
            name="Semester Work Plan",
        )
        version = TemplateVersion.all_objects.create(
            school=self.school, template=template, version=1
        )
        version.status = TemplateVersion.Status.PUBLISHED
        version.effective_from = date(2026, 8, 1)
        version.save()

    def create_test_plan(self):
        with tenant_scope(self.school):
            return create_work_plan(
                school=self.school,
                author=self.teacher,
                assignment=self.assignment,
                academic_year=self.academic_year,
                term=self.term,
                scheme=self.scheme,
            )

    def test_setup_form_filters_and_validates_active_assignments(self):
        with tenant_scope(self.school):
            form = WorkPlanCreateForm(school=self.school, user=self.teacher)
            self.assertEqual(list(form.fields["assignment"].queryset), [self.assignment])

            # Incompatible scheme validation
            invalid_data = {
                "assignment": self.assignment.pk,
                "academic_year": self.academic_year.pk,
                "term": self.term.pk,
                "scheme": self.science_scheme.pk,
            }
            bound_form = WorkPlanCreateForm(invalid_data, school=self.school, user=self.teacher)
            self.assertFalse(bound_form.is_valid())
            self.assertIn("scheme", bound_form.errors)

            # Valid scheme
            valid_data = {
                "assignment": self.assignment.pk,
                "academic_year": self.academic_year.pk,
                "term": self.term.pk,
                "scheme": self.scheme.pk,
            }
            valid_form = WorkPlanCreateForm(valid_data, school=self.school, user=self.teacher)
            self.assertTrue(valid_form.is_valid())

    def test_creation_initializes_lessons_per_week(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            w1 = plan.weeks.get(sequence=1)
            w_break = plan.weeks.get(sequence=8)
            self.assertEqual(w1.lessons_per_week, 1)
            self.assertTrue(w1.is_instructional)
            self.assertEqual(w_break.lessons_per_week, 0)
            self.assertFalse(w_break.is_instructional)

    def test_week_validation_for_subtopic_and_non_instructional_weeks(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            w1 = plan.weeks.get(sequence=1)
            w_break = plan.weeks.get(sequence=8)

            # Unit from wrong topic fails
            w1.topic = self.topic1
            w1.subtopic = self.unit3  # belongs to topic2
            with self.assertRaises(ValidationError):
                w1.full_clean()

            # Unit without topic fails
            w1.topic = None
            w1.subtopic = self.unit1
            with self.assertRaises(ValidationError):
                w1.full_clean()

            # Valid unit for topic succeeds
            w1.topic = self.topic1
            w1.subtopic = self.unit1
            w1.full_clean()

            # Non-instructional week cannot have topic or unit or >0 lessons
            w_break.topic = self.topic1
            with self.assertRaises(ValidationError):
                w_break.full_clean()

            w_break.topic = None
            w_break.lessons_per_week = 3
            with self.assertRaises(ValidationError):
                w_break.full_clean()

    def test_cross_unit_and_cross_topic_objectives_are_permitted(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            weeks = list(plan.weeks.order_by("sequence"))
            w1 = weeks[0]
            w2 = weeks[1]
            w_break = weeks[2]

            # Set week 1 primary topic to topic1 and primary unit to unit1
            # Add objectives from unit1, unit2 (cross-unit), topic2 (cross-topic), and general
            updates = [
                {
                    "id": w1.pk,
                    "topic_id": self.topic1.pk,
                    "subtopic_id": self.unit1.pk,
                    "lessons_per_week": 4,
                    "objectives": [
                        self.obj_unit1.pk,
                        self.obj_unit2.pk,
                        self.obj_topic2.pk,
                        self.obj_general.pk,
                    ],
                    "remarks": "Covering core number skills",
                },
                {
                    "id": w2.pk,
                    "topic_id": self.topic2.pk,
                    "lessons_per_week": 3,
                    "objectives": [],
                },
                {"id": w_break.pk, "remarks": "School closed"},
            ]

            saved = save_work_plan(
                plan=plan,
                actor=self.teacher,
                revision=1,
                resources="Worksheets",
                week_updates=updates,
            )
            self.assertEqual(saved.revision, 2)
            w1_reloaded = WorkPlanWeek.objects.get(pk=w1.pk)
            self.assertEqual(w1_reloaded.topic, self.topic1)
            self.assertEqual(w1_reloaded.subtopic, self.unit1)
            self.assertEqual(w1_reloaded.lessons_per_week, 4)
            self.assertEqual(w1_reloaded.objective_selections.count(), 4)

    def test_cross_scheme_objective_is_rejected(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            w1 = plan.weeks.get(sequence=1)
            obj_sel = WorkPlanWeekObjective(
                school=self.school,
                work_plan_week=w1,
                objective=self.foreign_obj,
            )
            with self.assertRaises(ValidationError):
                obj_sel.full_clean()

    def test_batch_objective_deduplication(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            weeks = list(plan.weeks.order_by("sequence"))
            # Pass duplicate objective IDs in payload
            updates = [
                {
                    "id": weeks[0].pk,
                    "topic_id": self.topic1.pk,
                    "subtopic_id": self.unit1.pk,
                    "lessons_per_week": 3,
                    "objectives": [self.obj_unit1.pk, self.obj_unit1.pk, self.obj_unit2.pk],
                },
                {"id": weeks[1].pk, "lessons_per_week": 2, "objectives": []},
                {"id": weeks[2].pk, "objectives": []},
            ]
            save_work_plan(
                plan=plan, actor=self.teacher, revision=1, resources="", week_updates=updates
            )
            self.assertEqual(weeks[0].objective_selections.count(), 2)

    def test_pdf_render_with_lessons_and_units(self):
        plan = self.create_test_plan()
        with tenant_scope(self.school):
            weeks = list(plan.weeks.order_by("sequence"))
            updates = [
                {
                    "id": weeks[0].pk,
                    "topic_id": self.topic1.pk,
                    "subtopic_id": self.unit1.pk,
                    "lessons_per_week": 5,
                    "objectives": [self.obj_unit1.pk, self.obj_unit2.pk],
                    "remarks": "Exam prep",
                },
                {"id": weeks[1].pk, "lessons_per_week": 4, "objectives": []},
                {"id": weeks[2].pk, "objectives": []},
            ]
            save_work_plan(
                plan=plan,
                actor=self.teacher,
                revision=1,
                resources="Textbook A",
                week_updates=updates,
            )
            output = BytesIO()
            render_work_plan(plan, output)
            self.assertTrue(output.getvalue().startswith(b"%PDF"))
