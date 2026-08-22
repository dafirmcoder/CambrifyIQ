from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)
from apps.planning.models import (
    PlanningTemplate,
    TemplateVersion,
    WorkPlan,
)
from apps.planning.pdf import render_work_plan
from apps.planning.services import (
    calculate_work_plan_coverage,
    create_work_plan,
    get_curriculum_coverage_data,
    get_previous_covered_objective_ids,
    save_work_plan,
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


class WorkPlanCoverageTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(
            name="Cambridge International", slug="cambridge-int", code="CIA01"
        )
        self.other_school = School.objects.create(
            name="Other Academy", slug="other-acad", code="OTH01"
        )

        self.teacher1 = User.objects.create_user(
            "teacher1@cia.local", "Password!123", full_name="Teacher One"
        )
        self.teacher2 = User.objects.create_user(
            "teacher2@cia.local", "Password!123", full_name="Teacher Two"
        )

        Membership.objects.create(
            school=self.school, user=self.teacher1, role=Membership.Role.TEACHER
        )
        Membership.objects.create(
            school=self.school, user=self.teacher2, role=Membership.Role.TEACHER
        )

        self.academic_year = AcademicYear.all_objects.create(
            school=self.school,
            name="2026/2027",
            starts_on=date(2026, 8, 1),
            ends_on=date(2027, 7, 31),
        )
        # Term 1 (earlier term)
        self.term1 = Term.all_objects.create(
            school=self.school,
            academic_year=self.academic_year,
            name="Term 1",
            sequence=1,
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 12, 15),
        )
        # Term 2 (later term)
        self.term2 = Term.all_objects.create(
            school=self.school,
            academic_year=self.academic_year,
            name="Term 2",
            sequence=2,
            starts_on=date(2027, 1, 10),
            ends_on=date(2027, 4, 15),
        )

        # Calendar weeks for Term 1
        self.t1_w1 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term1,
            sequence=1,
            starts_on=date(2026, 8, 3),
            ends_on=date(2026, 8, 7),
            is_instructional=True,
        )
        self.t1_w2 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term1,
            sequence=2,
            starts_on=date(2026, 8, 10),
            ends_on=date(2026, 8, 14),
            is_instructional=True,
        )

        # Calendar weeks for Term 2
        self.t2_w1 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term2,
            sequence=1,
            starts_on=date(2027, 1, 11),
            ends_on=date(2027, 1, 15),
            is_instructional=True,
        )
        self.t2_w2 = CalendarWeek.all_objects.create(
            school=self.school,
            term=self.term2,
            sequence=2,
            starts_on=date(2027, 1, 18),
            ends_on=date(2027, 1, 22),
            is_instructional=True,
        )

        # Subjects and Classes
        self.math_subject = Subject.all_objects.create(
            school=self.school, name="Mathematics", code="MAT", cambridge_code="0845"
        )
        self.science_subject = Subject.all_objects.create(
            school=self.school, name="Science", code="SCI", cambridge_code="0846"
        )

        self.class_7a = SchoolClass.all_objects.create(
            school=self.school, name="Year 7A", year_group="Stage 7"
        )
        self.class_7b = SchoolClass.all_objects.create(
            school=self.school, name="Year 7B", year_group="Stage 7"
        )

        # Assignments
        self.assignment_7a_math_t1 = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher1,
            subject=self.math_subject,
            school_class=self.class_7a,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        # Teacher 2 takes over Year 7A Math in Term 2
        self.assignment_7a_math_t2 = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher2,
            subject=self.math_subject,
            school_class=self.class_7a,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        self.assignment_7b_math = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher1,
            subject=self.math_subject,
            school_class=self.class_7b,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        self.assignment_7a_science = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=self.teacher1,
            subject=self.science_subject,
            school_class=self.class_7a,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        # Framework & Schemes
        self.framework = CurriculumFramework.objects.create(
            code="CLS", name="Cambridge Lower Secondary"
        )
        self.math_scheme = SchemeOfWork.objects.create(
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

        # Math Topics & Units
        # Topic 1: Numbers (2 objectives in unit 1, 1 general)
        self.topic_numbers = Topic.objects.create(
            scheme=self.math_scheme, title="Number and Calculation", sequence=1
        )
        self.unit_integers = Subtopic.objects.create(
            topic=self.topic_numbers, title="Integers and Powers", sequence=1
        )
        self.obj_num_1 = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_numbers,
            subtopic=self.unit_integers,
            code="7Nc.01",
            text="Understand powers.",
            sequence=1,
        )
        self.obj_num_2 = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_numbers,
            subtopic=self.unit_integers,
            code="7Nc.02",
            text="Calculate roots.",
            sequence=2,
        )
        self.obj_num_gen = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_numbers,
            subtopic=None,
            code="7Nc.00",
            text="General number sense.",
            sequence=3,
        )

        # Topic 2: Algebra (2 objectives in unit 1)
        self.topic_algebra = Topic.objects.create(
            scheme=self.math_scheme, title="Algebra", sequence=2
        )
        self.unit_expressions = Subtopic.objects.create(
            topic=self.topic_algebra, title="Expressions and Formulae", sequence=1
        )
        self.obj_alg_1 = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_algebra,
            subtopic=self.unit_expressions,
            code="7Ae.01",
            text="Simplify expressions.",
            sequence=1,
        )
        self.obj_alg_2 = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_algebra,
            subtopic=self.unit_expressions,
            code="7Ae.02",
            text="Expand brackets.",
            sequence=2,
        )

        # Topic 3: Geometry (1 objective)
        self.topic_geometry = Topic.objects.create(
            scheme=self.math_scheme, title="Geometry", sequence=3
        )
        self.obj_geo_1 = LearningObjective.objects.create(
            scheme=self.math_scheme,
            topic=self.topic_geometry,
            subtopic=None,
            code="7G.01",
            text="Angles on lines.",
            sequence=1,
        )

        # Template setup
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

    def test_approved_previous_work_plan_hides_its_objectives(self):
        """Approved previous plan objectives are identified as previously covered."""
        with tenant_scope(self.school):
            # Term 1 plan: Covers obj_num_1 and obj_num_2
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="Textbook 1",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_numbers.pk,
                        "subtopic_id": self.unit_integers.pk,
                        "lessons_per_week": 4,
                        "objectives": [self.obj_num_1.pk, self.obj_num_2.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 2, "objectives": []},
                ],
            )
            t1_plan.status = WorkPlan.Status.APPROVED
            t1_plan.save()

            # Now create Term 2 plan (Teacher 2 takes over Year 7A Math)
            t2_plan = create_work_plan(
                school=self.school,
                author=self.teacher2,
                assignment=self.assignment_7a_math_t2,
                academic_year=self.academic_year,
                term=self.term2,
                scheme=self.math_scheme,
            )

            prev_obj_ids = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
                exclude_plan_id=t2_plan.id,
            )
            self.assertEqual(prev_obj_ids, {self.obj_num_1.pk, self.obj_num_2.pk})

            # Check curriculum coverage data
            curriculum_data = get_curriculum_coverage_data(t2_plan)
            cov = curriculum_data["coverage"]

            # Total scheme has 6 objectives: 3 in Numbers, 2 in Algebra, 1 in Geometry
            self.assertEqual(cov["total_objectives"], 6)
            self.assertEqual(cov["previously_covered_objectives"], 2)
            self.assertEqual(cov["previous_objective_percent"], 33.3)
            self.assertEqual(cov["current_plan_objectives"], 0)
            self.assertEqual(cov["projected_covered_objectives"], 2)
            self.assertEqual(cov["remaining_objectives"], 4)

            # Check Topic data
            num_topic_data = next(
                t for t in curriculum_data["topics"] if t["id"] == str(self.topic_numbers.pk)
            )
            self.assertEqual(num_topic_data["total_objectives_count"], 3)
            self.assertEqual(num_topic_data["previously_covered_count"], 2)
            self.assertEqual(num_topic_data["available_objectives_count"], 1)
            self.assertFalse(num_topic_data["is_fully_covered"])

            # Check Unit data: Integers had 2 objectives, both covered -> is_fully_covered
            int_unit_data = next(
                u for u in curriculum_data["units"] if u["id"] == str(self.unit_integers.pk)
            )
            self.assertEqual(int_unit_data["total_objectives_count"], 2)
            self.assertEqual(int_unit_data["previously_covered_count"], 2)
            self.assertEqual(int_unit_data["available_objectives_count"], 0)
            self.assertTrue(int_unit_data["is_fully_covered"])

    def test_archived_previous_work_plan_hides_its_objectives(self):
        """Archived previous plans are also eligible for coverage history."""
        with tenant_scope(self.school):
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_algebra.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_alg_1.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )
            t1_plan.status = WorkPlan.Status.ARCHIVED
            t1_plan.save()

            prev_obj_ids = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
            )
            self.assertIn(self.obj_alg_1.pk, prev_obj_ids)

    def test_draft_previous_work_plan_does_not_hide_objectives(self):
        """Draft plans do not reduce available objectives."""
        with tenant_scope(self.school):
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_algebra.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_alg_1.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )
            # Stays in DRAFT status
            prev_obj_ids = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
            )
            self.assertEqual(len(prev_obj_ids), 0)

    def test_plan_from_different_class_does_not_hide_objectives(self):
        """Coverage in Year 7B does not hide objectives for Year 7A."""
        with tenant_scope(self.school):
            t1_plan_7b = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7b_math,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan_7b.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan_7b,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_numbers.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_num_1.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )
            t1_plan_7b.status = WorkPlan.Status.APPROVED
            t1_plan_7b.save()

            # Query coverage for Year 7A
            prev_obj_ids_7a = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
            )
            self.assertEqual(len(prev_obj_ids_7a), 0)

    def test_plan_from_different_subject_does_not_hide_objectives(self):
        """Coverage in Science does not affect Mathematics."""
        with tenant_scope(self.school):
            t1_sci_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_science,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.science_scheme,
            )
            t1_sci_plan.status = WorkPlan.Status.APPROVED
            t1_sci_plan.save()

            prev_obj_ids_math = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
            )
            self.assertEqual(len(prev_obj_ids_math), 0)

    def test_duplicate_objective_in_previous_weeks_counted_once(self):
        """Using an objective across multiple weeks in Term 1 still counts as 1 unique objective."""
        with tenant_scope(self.school):
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            # Use obj_num_1 in both week 1 and week 2
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_numbers.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_num_1.pk],
                    },
                    {
                        "id": t1_weeks[1].pk,
                        "topic_id": self.topic_numbers.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_num_1.pk],
                    },
                ],
            )
            t1_plan.status = WorkPlan.Status.APPROVED
            t1_plan.save()

            prev_obj_ids = get_previous_covered_objective_ids(
                school=self.school,
                school_class=self.class_7a,
                subject=self.math_subject,
                scheme=self.math_scheme,
                before_date=self.term2.starts_on,
            )
            self.assertEqual(len(prev_obj_ids), 1)

    def test_fully_covered_topic_excluded_from_selectors(self):
        """When all objectives of Topic 3 (Geometry) are covered, it is marked fully covered."""
        with tenant_scope(self.school):
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_geometry.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_geo_1.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )
            t1_plan.status = WorkPlan.Status.APPROVED
            t1_plan.save()

            t2_plan = create_work_plan(
                school=self.school,
                author=self.teacher2,
                assignment=self.assignment_7a_math_t2,
                academic_year=self.academic_year,
                term=self.term2,
                scheme=self.math_scheme,
            )
            curriculum_data = get_curriculum_coverage_data(t2_plan)
            geo_topic = next(
                t for t in curriculum_data["topics"] if t["id"] == str(self.topic_geometry.pk)
            )
            self.assertTrue(geo_topic["is_fully_covered"])
            self.assertEqual(geo_topic["available_objectives_count"], 0)

    def test_current_and_previous_coverage_combined_union(self):
        """Current plan adding objectives updates projected coverage."""
        with tenant_scope(self.school):
            # Term 1 covered obj_num_1
            t1_plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=self.assignment_7a_math_t1,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=self.math_scheme,
            )
            t1_weeks = list(t1_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t1_plan,
                actor=self.teacher1,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t1_weeks[0].pk,
                        "topic_id": self.topic_numbers.pk,
                        "lessons_per_week": 3,
                        "objectives": [self.obj_num_1.pk],
                    },
                    {"id": t1_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )
            t1_plan.status = WorkPlan.Status.APPROVED
            t1_plan.save()

            # Term 2 plan adds obj_alg_1 and obj_alg_2
            t2_plan = create_work_plan(
                school=self.school,
                author=self.teacher2,
                assignment=self.assignment_7a_math_t2,
                academic_year=self.academic_year,
                term=self.term2,
                scheme=self.math_scheme,
            )
            t2_weeks = list(t2_plan.weeks.order_by("sequence"))
            save_work_plan(
                plan=t2_plan,
                actor=self.teacher2,
                revision=1,
                resources="",
                week_updates=[
                    {
                        "id": t2_weeks[0].pk,
                        "topic_id": self.topic_algebra.pk,
                        "lessons_per_week": 4,
                        "objectives": [self.obj_alg_1.pk, self.obj_alg_2.pk],
                    },
                    {"id": t2_weeks[1].pk, "lessons_per_week": 1, "objectives": []},
                ],
            )

            cov = calculate_work_plan_coverage(t2_plan)
            # Total 6 objectives
            # Previous: 1 (obj_num_1) -> 16.7%
            # Current: 2 (obj_alg_1, obj_alg_2)
            # Projected: 3 (obj_num_1, obj_alg_1, obj_alg_2) -> 50.0%
            self.assertEqual(cov["total_objectives"], 6)
            self.assertEqual(cov["previously_covered_objectives"], 1)
            self.assertEqual(cov["previous_objective_percent"], 16.7)
            self.assertEqual(cov["current_plan_objectives"], 2)
            self.assertEqual(cov["projected_covered_objectives"], 3)
            self.assertEqual(cov["projected_objective_percent"], 50.0)
            self.assertEqual(cov["remaining_objectives"], 3)
            # Topics covered: Numbers (via prev) and Algebra (via curr) -> 2 / 3 topics = 66.7%
            self.assertEqual(cov["covered_topics"], 2)
            self.assertEqual(cov["total_topics"], 3)
            self.assertEqual(cov["projected_topic_percent"], 66.7)

            # PDF Render verification
            output = BytesIO()
            render_work_plan(t2_plan, output)
            self.assertTrue(output.getvalue().startswith(b"%PDF"))

    def test_zero_objective_scheme_handled_safely(self):
        """A scheme with zero objectives produces 0.0% without division error."""
        with tenant_scope(self.school):
            empty_scheme = SchemeOfWork.objects.create(
                framework=self.framework,
                subject_code="9999",
                subject_name="Empty Subject",
                year_group="Stage 7",
                title="Empty Subject Stage 7",
                is_active=True,
            )
            empty_subject = Subject.all_objects.create(
                school=self.school, name="Empty Subject", code="EMP", cambridge_code="9999"
            )
            empty_assignment = TeacherAssignment.all_objects.create(
                school=self.school,
                teacher=self.teacher1,
                subject=empty_subject,
                school_class=self.class_7a,
                effective_from=date(2026, 1, 1),
                is_active=True,
            )
            plan = create_work_plan(
                school=self.school,
                author=self.teacher1,
                assignment=empty_assignment,
                academic_year=self.academic_year,
                term=self.term1,
                scheme=empty_scheme,
            )
            cov = calculate_work_plan_coverage(plan)
            self.assertEqual(cov["total_objectives"], 0)
            self.assertEqual(cov["previous_objective_percent"], 0.0)
            self.assertEqual(cov["projected_objective_percent"], 0.0)
            self.assertEqual(cov["projected_topic_percent"], 0.0)
