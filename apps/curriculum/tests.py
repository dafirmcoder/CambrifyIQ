"""Curriculum core tests — CAMS plan sections 7.1, 8.4, 10.5 and 12."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.core.tenant import tenant_scope
from apps.curriculum import services
from apps.curriculum.models import (
    AssessmentObjective,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)
from apps.schools.models import (
    AcademicYear,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)

User = get_user_model()
TODAY = timezone.localdate()


class CurriculumFixture:
    """Builds one fully populated school so tests read clearly."""

    def build_school(self, name, code):
        director = User.objects.create_user(
            f"director@{code.lower()}.example", "StrongPass!246", full_name=f"{name} Director"
        )
        school = School.objects.create(name=name, slug=code.lower(), code=code, created_by=director)
        Membership.objects.create(
            school=school, user=director, role=Membership.Role.DIRECTOR, is_primary=True
        )
        year = AcademicYear.all_objects.create(
            school=school,
            name="2026/2027",
            starts_on=date(2026, 8, 1),
            ends_on=date(2027, 6, 30),
            is_current=True,
        )
        term = Term.all_objects.create(
            school=school,
            academic_year=year,
            name="Semester 1",
            sequence=1,
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 12, 18),
        )
        subject = Subject.all_objects.create(school=school, name="Science", code="SCI")
        school_class = SchoolClass.all_objects.create(
            school=school, name="Year 8", year_group="Y8", boys_count=14, girls_count=12
        )
        scheme = SchemeOfWork.all_objects.create(
            school=school,
            subject=subject,
            school_class=school_class,
            academic_year=year,
            term=term,
            title="Science Year 8 Semester 1",
            code=f"{code}-SCI-Y8-S1",
            status=SchemeOfWork.Status.PUBLISHED,
        )
        topic = Topic.all_objects.create(
            school=school, scheme=scheme, code="T1", title="Forces and motion", sequence=1
        )
        subtopic = Subtopic.all_objects.create(
            school=school, topic=topic, code="T1.1", title="Speed", sequence=1
        )
        objective = LearningObjective.all_objects.create(
            school=school,
            topic=topic,
            subtopic=subtopic,
            code="8Ps.01",
            text="Calculate average speed from distance and time.",
            sequence=1,
        )
        topic_level_objective = LearningObjective.all_objects.create(
            school=school,
            topic=topic,
            code="8Ps.02",
            text="Describe motion using distance-time graphs.",
            sequence=2,
        )
        return {
            "school": school,
            "director": director,
            "year": year,
            "term": term,
            "subject": subject,
            "class": school_class,
            "scheme": scheme,
            "topic": topic,
            "subtopic": subtopic,
            "objective": objective,
            "topic_objective": topic_level_objective,
        }

    def add_teacher(self, data, email, *, assigned=True):
        user = User.objects.create_user(email, "StrongPass!246", full_name=email.split("@")[0])
        membership = Membership.objects.create(
            school=data["school"], user=user, role=Membership.Role.TEACHER
        )
        if assigned:
            TeacherAssignment.all_objects.create(
                school=data["school"],
                teacher=user,
                subject=data["subject"],
                school_class=data["class"],
                effective_from=TODAY - timedelta(days=30),
            )
        return membership

    def add_leader(self, data, role, email):
        user = User.objects.create_user(email, "StrongPass!246", full_name=email.split("@")[0])
        return Membership.objects.create(school=data["school"], user=user, role=role)


class ModelIntegrityTests(TestCase, CurriculumFixture):
    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")

    def test_scheme_rejects_a_term_from_another_year(self):
        other_year = AcademicYear.all_objects.create(
            school=self.data["school"],
            name="2027/2028",
            starts_on=date(2027, 8, 1),
            ends_on=date(2028, 6, 30),
        )
        scheme = SchemeOfWork(
            school=self.data["school"],
            subject=self.data["subject"],
            school_class=self.data["class"],
            academic_year=other_year,
            term=self.data["term"],
            title="Mismatched",
            code="BAD-1",
        )
        with self.assertRaises(ValidationError) as ctx:
            scheme.full_clean()
        self.assertIn("term", ctx.exception.message_dict)

    def test_cross_school_scheme_relations_are_rejected(self):
        other = self.build_school("Beta Cambridge", "BETA")
        scheme = SchemeOfWork(
            school=self.data["school"],
            subject=other["subject"],
            school_class=self.data["class"],
            academic_year=self.data["year"],
            title="Leaky",
            code="BAD-2",
        )
        with self.assertRaises(ValidationError) as ctx:
            scheme.full_clean()
        self.assertIn("subject", ctx.exception.message_dict)

    def test_objective_subtopic_must_belong_to_its_topic(self):
        other_topic = Topic.all_objects.create(
            school=self.data["school"],
            scheme=self.data["scheme"],
            code="T2",
            title="Energy",
            sequence=2,
        )
        objective = LearningObjective(
            school=self.data["school"],
            topic=other_topic,
            subtopic=self.data["subtopic"],
            code="8Pe.01",
            text="Mismatch",
        )
        with self.assertRaises(ValidationError) as ctx:
            objective.full_clean()
        self.assertIn("subtopic", ctx.exception.message_dict)

    def test_effective_window_rejects_reversed_dates(self):
        topic = Topic(
            school=self.data["school"],
            scheme=self.data["scheme"],
            code="T9",
            title="Bad window",
            effective_from=date(2026, 9, 1),
            effective_until=date(2026, 8, 1),
        )
        with self.assertRaises(ValidationError) as ctx:
            topic.full_clean()
        self.assertIn("effective_until", ctx.exception.message_dict)

    def test_objective_label_preserves_code_and_text(self):
        self.assertTrue(self.data["objective"].label.startswith("8Ps.01: "))

    def test_expired_rows_are_not_selectable_but_stay_readable(self):
        objective = self.data["objective"]
        objective.effective_until = TODAY - timedelta(days=1)
        objective.save()
        self.assertFalse(objective.is_selectable())
        with tenant_scope(self.data["school"].pk):
            self.assertTrue(LearningObjective.objects.filter(pk=objective.pk).exists())
            self.assertFalse(
                LearningObjective.objects.selectable().filter(pk=objective.pk).exists()
            )


class OptionScopingTests(TestCase, CurriculumFixture):
    """10.5: an undiscoverable value must also be unusable."""

    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")
        self.school = self.data["school"]
        self.teacher = self.add_teacher(self.data, "teacher@lis.example")
        self.coordinator = self.add_leader(
            self.data, Membership.Role.COORDINATOR, "coord@lis.example"
        )

    def test_assigned_teacher_sees_their_scheme(self):
        with tenant_scope(self.school.pk):
            schemes = services.visible_schemes(self.teacher)
        self.assertEqual([s.pk for s in schemes], [self.data["scheme"].pk])

    def test_unassigned_teacher_sees_nothing(self):
        stranger = self.add_teacher(self.data, "stranger@lis.example", assigned=False)
        with tenant_scope(self.school.pk):
            self.assertEqual(services.visible_schemes(stranger).count(), 0)

    def test_teacher_cannot_reach_topics_of_an_unassigned_scheme(self):
        stranger = self.add_teacher(self.data, "stranger2@lis.example", assigned=False)
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            services.topic_options(stranger, self.data["scheme"].pk)

    def test_expired_assignment_removes_access(self):
        with tenant_scope(self.school.pk):
            TeacherAssignment.objects.filter(teacher=self.teacher.user).update(
                effective_until=TODAY - timedelta(days=1)
            )
            self.assertEqual(services.visible_schemes(self.teacher).count(), 0)

    def test_coordinator_sees_all_school_schemes(self):
        with tenant_scope(self.school.pk):
            self.assertEqual(services.visible_schemes(self.coordinator).count(), 1)

    def test_draft_schemes_are_not_selectable(self):
        with tenant_scope(self.school.pk):
            SchemeOfWork.objects.filter(pk=self.data["scheme"].pk).update(
                status=SchemeOfWork.Status.DRAFT
            )
            self.assertEqual(services.visible_schemes(self.coordinator).count(), 0)

    def test_subtopic_objectives_include_topic_level_rows(self):
        with tenant_scope(self.school.pk):
            options = services.learning_objective_options(
                self.teacher, subtopic_id=self.data["subtopic"].pk
            )
        codes = sorted(item.code for item in options)
        self.assertEqual(codes, ["8Ps.01", "8Ps.02"])

    def test_inactive_objective_disappears_from_options(self):
        with tenant_scope(self.school.pk):
            LearningObjective.objects.filter(pk=self.data["objective"].pk).update(is_active=False)
            options = services.learning_objective_options(
                self.teacher, topic_id=self.data["topic"].pk
            )
        self.assertNotIn("8Ps.01", [item.code for item in options])

    def test_assessment_objectives_require_an_assigned_subject(self):
        other_subject = Subject.all_objects.create(school=self.school, name="History", code="HIS")
        AssessmentObjective.all_objects.create(
            school=self.school, subject=other_subject, code="AO1", text="Recall"
        )
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            services.assessment_objective_options(self.teacher, other_subject.pk)


class SubmittedOptionValidationTests(TestCase, CurriculumFixture):
    """12: the server validates submitted option IDs; client labels are not trusted."""

    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")
        self.school = self.data["school"]
        self.teacher = self.add_teacher(self.data, "teacher@lis.example")

    def test_authorised_objectives_resolve_in_order(self):
        with tenant_scope(self.school.pk):
            resolved = services.resolve_selected_objectives(
                self.teacher,
                [self.data["topic_objective"].pk, self.data["objective"].pk],
                topic_id=self.data["topic"].pk,
            )
        self.assertEqual([item.code for item in resolved], ["8Ps.02", "8Ps.01"])

    def test_objective_from_another_school_is_rejected(self):
        other = self.build_school("Beta Cambridge", "BETA")
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            services.resolve_selected_objectives(
                self.teacher, [other["objective"].pk], topic_id=self.data["topic"].pk
            )

    def test_deactivated_objective_cannot_be_submitted(self):
        with tenant_scope(self.school.pk):
            LearningObjective.objects.filter(pk=self.data["objective"].pk).update(is_active=False)
            with self.assertRaises(PermissionDenied):
                services.resolve_selected_objectives(
                    self.teacher, [self.data["objective"].pk], topic_id=self.data["topic"].pk
                )


class ContentPermissionTests(TestCase, CurriculumFixture):
    """6.2 permission matrix: only Coordinator and Head edit curriculum."""

    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")

    def test_coordinator_and_head_may_edit(self):
        for role, email in (
            (Membership.Role.COORDINATOR, "c@lis.example"),
            (Membership.Role.HEAD, "h@lis.example"),
        ):
            membership = self.add_leader(self.data, role, email)
            self.assertIs(services.assert_can_edit_curriculum(membership), membership)

    def test_teacher_and_director_may_not_edit(self):
        teacher = self.add_teacher(self.data, "t@lis.example")
        director = Membership.objects.get(school=self.data["school"], role=Membership.Role.DIRECTOR)
        for membership in (teacher, director):
            with self.assertRaises(PermissionDenied):
                services.assert_can_edit_curriculum(membership)

    def test_suspended_membership_loses_access(self):
        coordinator = self.add_leader(self.data, Membership.Role.COORDINATOR, "s@lis.example")
        coordinator.status = Membership.Status.SUSPENDED
        with self.assertRaises(PermissionDenied):
            services.assert_can_edit_curriculum(coordinator)


class TenantIsolationTests(TestCase, CurriculumFixture):
    """12: adversarial cross-school access must return nothing."""

    def test_curriculum_is_invisible_across_schools(self):
        alpha = self.build_school("Alpha Cambridge", "ALPHA")
        beta = self.build_school("Beta Cambridge", "BETA")
        with tenant_scope(beta["school"].pk):
            self.assertFalse(SchemeOfWork.objects.filter(pk=alpha["scheme"].pk).exists())
            self.assertFalse(Topic.objects.filter(pk=alpha["topic"].pk).exists())
            self.assertFalse(LearningObjective.objects.filter(pk=alpha["objective"].pk).exists())

    def test_managers_fail_closed_without_tenant_context(self):
        self.build_school("Gamma Cambridge", "GAMMA")
        self.assertEqual(SchemeOfWork.objects.count(), 0)
        self.assertEqual(Topic.objects.count(), 0)
        self.assertEqual(LearningObjective.objects.count(), 0)
