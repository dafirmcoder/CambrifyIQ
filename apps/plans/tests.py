"""Plan workflow, validation and rendering tests (plan 7.2–7.5, 8.5, 8.6, 8.8)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.curriculum.models import LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.planning import services as planning_services
from apps.planning.models import PlanType
from apps.plans import pdf, services, validation, workflow
from apps.plans.models import (
    GeneratedDocument,
    LessonPlan,
    PlanReview,
    PlanState,
    WorkPlan,
)
from apps.schools.calendar import generate_weeks
from apps.schools.models import (
    AcademicYear,
    AuditLog,
    CalendarWeek,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)

User = get_user_model()


class PlanFixture:
    """A school with a published template, curriculum and an assigned teacher."""

    def build(self, name="Leera International", code="LIS"):
        director = User.objects.create_user(
            f"director@{code.lower()}.example", "StrongPass!246", full_name="Dora Director"
        )
        school = School.objects.create(name=name, slug=code.lower(), code=code, created_by=director)
        director_m = Membership.objects.create(
            school=school, user=director, role=Membership.Role.DIRECTOR, is_primary=True
        )
        head_user = User.objects.create_user(
            f"head@{code.lower()}.example", "StrongPass!246", full_name="Hana Head"
        )
        head = Membership.objects.create(school=school, user=head_user, role=Membership.Role.HEAD)
        teacher_user = User.objects.create_user(
            f"teacher@{code.lower()}.example", "StrongPass!246", full_name="Tato Teacher"
        )
        teacher = Membership.objects.create(
            school=school, user=teacher_user, role=Membership.Role.TEACHER
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
            starts_on=date(2026, 8, 3),
            ends_on=date(2026, 12, 18),
        )
        subject = Subject.all_objects.create(school=school, name="Science", code="SCI")
        school_class = SchoolClass.all_objects.create(
            school=school, name="Year 8", year_group="Y8", boys_count=14, girls_count=12
        )
        assignment = TeacherAssignment.all_objects.create(
            school=school,
            teacher=teacher_user,
            subject=subject,
            school_class=school_class,
            effective_from=date(2026, 1, 1),
        )
        scheme = SchemeOfWork.all_objects.create(
            school=school,
            subject=subject,
            school_class=school_class,
            academic_year=year,
            term=term,
            title="Science Year 8",
            code=f"{code}-SCI-Y8",
            status=SchemeOfWork.Status.PUBLISHED,
        )
        topic = Topic.all_objects.create(
            school=school, scheme=scheme, code="T1", title="Forces", sequence=1
        )
        subtopic = Subtopic.all_objects.create(
            school=school, topic=topic, code="T1.1", title="Speed", sequence=1
        )
        objective = LearningObjective.all_objects.create(
            school=school,
            topic=topic,
            subtopic=subtopic,
            code="8Ps.01",
            text="Calculate average speed.",
        )

        with tenant_scope(school.pk):
            for plan_type in (PlanType.LESSON_PLAN, PlanType.WORK_PLAN):
                version = planning_services.create_draft_version(
                    membership=director_m, plan_type=plan_type
                )
                planning_services.record_clean_master(
                    membership=director_m,
                    version=version,
                    filename=f"{plan_type}-clean.pdf",
                    checksum="a" * 64,
                    approved=True,
                )
                planning_services.approve_version(membership=director_m, version=version)
                planning_services.publish_version(membership=director_m, version=version)

        return {
            "school": school,
            "director": director_m,
            "head": head,
            "teacher": teacher,
            "year": year,
            "term": term,
            "subject": subject,
            "class": school_class,
            "assignment": assignment,
            "scheme": scheme,
            "topic": topic,
            "subtopic": subtopic,
            "objective": objective,
        }

    def make_lesson_plan(self, data, *, complete=True):
        with tenant_scope(data["school"].pk):
            plan = services.create_lesson_plan(
                membership=data["teacher"],
                assignment_id=data["assignment"].pk,
                lesson_date=date(2026, 9, 15),
            )
            if complete:
                services.save_lesson_plan(
                    membership=data["teacher"],
                    plan=plan,
                    subtopic_id=data["subtopic"].pk,
                    objective_ids=[data["objective"].pk],
                    boys_present=12,
                    girls_present=10,
                    main_teaching_activity="Investigate speed using trolleys and timers.",
                    assessment_ideas="Exit ticket calculating average speed.",
                )
        return plan


class CalendarTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()

    def test_term_generates_seventeen_weeks_with_fixed_events(self):
        weeks = generate_weeks(self.data["term"])
        self.assertEqual(len(weeks), 17)
        self.assertEqual(weeks[0].number, 1)
        self.assertEqual(weeks[14].event_label, "Revision Week")
        self.assertEqual(weeks[15].event_label, "Semester Assessments")
        self.assertEqual(weeks[16].event_label, "End of First Semester & PTC")
        self.assertFalse(weeks[14].is_teaching_week)

    def test_generation_is_idempotent(self):
        generate_weeks(self.data["term"])
        generate_weeks(self.data["term"])
        self.assertEqual(CalendarWeek.all_objects.filter(term=self.data["term"]).count(), 17)

    def test_weeks_start_on_monday_and_carry_month_labels(self):
        weeks = generate_weeks(self.data["term"])
        self.assertTrue(all(week.starts_on.weekday() == 0 for week in weeks))
        self.assertEqual(weeks[0].month_label, "August")


class WorkPlanTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()

    def create(self):
        with tenant_scope(self.data["school"].pk):
            return services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
                scheme=self.data["scheme"],
            )

    def test_creation_generates_all_week_rows(self):
        plan = self.create()
        rows = list(services.week_rows_for(plan))
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows[0].week_number, 1)
        self.assertEqual(rows[16].event_label, "End of First Semester & PTC")

    def test_duplicate_plan_for_same_term_is_refused(self):
        self.create()
        with self.assertRaises(ValidationError):
            self.create()

    def test_row_autosave_stores_objective_labels(self):
        plan = self.create()
        row = services.week_rows_for(plan).first()
        with tenant_scope(self.data["school"].pk):
            services.save_work_plan_row(
                membership=self.data["teacher"],
                plan=plan,
                row=row,
                objective_ids=[self.data["objective"].pk],
                remarks="Introduce practical work.",
            )
        row.refresh_from_db()
        self.assertEqual(row.objective_labels, ["8Ps.01: Calculate average speed."])
        self.assertEqual(row.topic_id, self.data["topic"].pk)
        self.assertEqual(row.remarks, "Introduce practical work.")

    def test_stale_revision_token_is_rejected(self):
        plan = self.create()
        row = services.week_rows_for(plan).first()
        with tenant_scope(self.data["school"].pk):
            services.save_work_plan_row(
                membership=self.data["teacher"], plan=plan, row=row, remarks="First"
            )
            with self.assertRaises(services.RevisionConflict):
                services.save_work_plan_row(
                    membership=self.data["teacher"],
                    plan=plan,
                    row=row,
                    base_revision=1,
                    remarks="Conflicting",
                )

    def test_another_teacher_cannot_save_the_plan(self):
        plan = self.create()
        row = services.week_rows_for(plan).first()
        other_user = User.objects.create_user(
            "other@lis.example", "StrongPass!246", full_name="Other"
        )
        other = Membership.objects.create(
            school=self.data["school"], user=other_user, role=Membership.Role.TEACHER
        )
        with tenant_scope(self.data["school"].pk), self.assertRaises(PermissionDenied):
            services.save_work_plan_row(membership=other, plan=plan, row=row, remarks="Intruder")

    def test_submission_requires_planned_weeks(self):
        plan = self.create()
        with tenant_scope(self.data["school"].pk), self.assertRaises(ValidationError):
            workflow.submit(membership=self.data["teacher"], plan=plan)


class LessonPlanTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()

    def test_attendance_total_is_computed(self):
        plan = self.make_lesson_plan(self.data)
        self.assertEqual(plan.attendance_total, 22)

    def test_attendance_cannot_exceed_the_roster(self):
        plan = self.make_lesson_plan(self.data, complete=False)
        with tenant_scope(self.data["school"].pk), self.assertRaises(ValidationError):
            services.save_lesson_plan(
                membership=self.data["teacher"], plan=plan, boys_present=99, girls_present=1
            )

    def test_attendance_over_roster_allowed_with_audited_exception(self):
        plan = self.make_lesson_plan(self.data, complete=False)
        with tenant_scope(self.data["school"].pk):
            services.save_lesson_plan(
                membership=self.data["teacher"],
                plan=plan,
                boys_present=16,
                girls_present=1,
                attendance_exception="Two visiting learners joined the lesson.",
            )
        plan.refresh_from_db()
        self.assertEqual(plan.boys_present, 16)

    def test_negative_attendance_is_refused(self):
        plan = self.make_lesson_plan(self.data, complete=False)
        with tenant_scope(self.data["school"].pk), self.assertRaises(ValidationError):
            services.save_lesson_plan(
                membership=self.data["teacher"], plan=plan, boys_present=-1, girls_present=0
            )

    def test_objectives_outside_scope_are_refused(self):
        other = self.build("Beta Cambridge", "BETA")
        plan = self.make_lesson_plan(self.data, complete=False)
        with tenant_scope(self.data["school"].pk), self.assertRaises(PermissionDenied):
            services.save_lesson_plan(
                membership=self.data["teacher"],
                plan=plan,
                subtopic_id=self.data["subtopic"].pk,
                objective_ids=[other["objective"].pk],
            )

    def test_incomplete_plan_lists_every_missing_field(self):
        plan = self.make_lesson_plan(self.data, complete=False)
        issues = validation.lesson_plan_issues(plan)
        self.assertTrue(any("LP-D01" in item for item in issues))
        self.assertTrue(any("LP-D02" in item for item in issues))
        self.assertTrue(any("LP-D04" in item for item in issues))
        self.assertTrue(any("LP-T01" in item for item in issues))
        self.assertTrue(any("LP-T02" in item for item in issues))
        # LP-T03 is optional and must never block submission.
        self.assertFalse(any("LP-T03" in item for item in issues))

    def test_complete_plan_has_no_issues(self):
        plan = self.make_lesson_plan(self.data)
        self.assertEqual(validation.lesson_plan_issues(plan), [])

    def test_overflow_produces_a_warning_not_an_error(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.data["school"].pk):
            services.save_lesson_plan(
                membership=self.data["teacher"],
                plan=plan,
                main_teaching_activity="word " * 900,
            )
        warnings = validation.overflow_warnings(plan)
        self.assertTrue(any("LP-T01" in item for item in warnings))
        self.assertEqual(validation.lesson_plan_issues(plan), [])

    def test_carry_forward_from_a_work_plan_row(self):
        with tenant_scope(self.data["school"].pk):
            work_plan = services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
            )
            row = services.week_rows_for(work_plan).first()
            services.save_work_plan_row(
                membership=self.data["teacher"],
                plan=work_plan,
                row=row,
                objective_ids=[self.data["objective"].pk],
            )
            row.refresh_from_db()
            lesson = services.create_lesson_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                lesson_date=date(2026, 9, 15),
                work_plan_row=row,
            )
        self.assertEqual(lesson.topic_id, self.data["topic"].pk)
        self.assertEqual(lesson.objective_labels, ["8Ps.01: Calculate average speed."])

    def test_lesson_date_outside_the_assignment_is_refused(self):
        with tenant_scope(self.data["school"].pk), self.assertRaises(PermissionDenied):
            services.create_lesson_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                lesson_date=date(2025, 1, 1),
            )


class WorkflowTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()
        self.plan = self.make_lesson_plan(self.data)

    def test_full_submit_review_approve_cycle(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            self.assertEqual(self.plan.state, PlanState.SUBMITTED)
            workflow.claim_review(membership=self.data["head"], plan=self.plan)
            self.assertEqual(self.plan.state, PlanState.UNDER_REVIEW)
            workflow.approve(membership=self.data["head"], plan=self.plan, comment="Good")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.state, PlanState.APPROVED)
        self.assertIsNotNone(self.plan.approved_at)
        self.assertEqual(self.plan.approved_by_id, self.data["head"].user_id)

    def test_return_requires_a_comment(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            with self.assertRaises(ValidationError):
                workflow.return_for_changes(
                    membership=self.data["head"], plan=self.plan, comment="  "
                )

    def test_return_then_resubmit(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            workflow.return_for_changes(
                membership=self.data["head"], plan=self.plan, comment="Add differentiation."
            )
            self.assertEqual(self.plan.state, PlanState.RETURNED)
            self.assertTrue(self.plan.is_editable)
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
        self.assertEqual(self.plan.state, PlanState.RESUBMITTED)

    def test_approved_plan_cannot_be_edited(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            workflow.approve(membership=self.data["head"], plan=self.plan)
            with self.assertRaises(ValidationError):
                services.save_lesson_plan(
                    membership=self.data["teacher"],
                    plan=self.plan,
                    notes_remarks="Sneaky edit",
                )

    def test_teacher_cannot_approve_their_own_plan(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            with self.assertRaises(PermissionDenied):
                workflow.approve(membership=self.data["teacher"], plan=self.plan)

    def test_coordinator_cannot_grant_final_approval(self):
        coordinator_user = User.objects.create_user(
            "coord@lis.example", "StrongPass!246", full_name="Cora Coordinator"
        )
        coordinator = Membership.objects.create(
            school=self.data["school"], user=coordinator_user, role=Membership.Role.COORDINATOR
        )
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            with self.assertRaises(PermissionDenied):
                workflow.approve(membership=coordinator, plan=self.plan)
            # A coordinator may still return the plan.
            workflow.return_for_changes(
                membership=coordinator, plan=self.plan, comment="Check the objectives."
            )
        self.assertEqual(self.plan.state, PlanState.RETURNED)

    def test_illegal_transition_is_refused(self):
        with tenant_scope(self.data["school"].pk), self.assertRaises(ValidationError):
            workflow.approve(membership=self.data["head"], plan=self.plan)

    def test_every_transition_is_recorded(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            workflow.return_for_changes(
                membership=self.data["head"], plan=self.plan, comment="More detail."
            )
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            workflow.approve(membership=self.data["head"], plan=self.plan)
            entries = list(workflow.history(self.plan))
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].action, PlanReview.Action.APPROVED)
        self.assertEqual(entries[0].previous_state, PlanState.RESUBMITTED)
        self.assertTrue(
            AuditLog.all_objects.filter(
                school=self.data["school"], action="lesson_plan.approved"
            ).exists()
        )

    def test_review_records_are_immutable(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            entry = workflow.history(self.plan).first()
            entry.comment = "tampered"
            with self.assertRaises(ValidationError):
                entry.save()
            with self.assertRaises(ValidationError):
                entry.delete()

    def test_archive_after_approval(self):
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            workflow.approve(membership=self.data["head"], plan=self.plan)
            workflow.archive(membership=self.data["head"], plan=self.plan)
        self.assertEqual(self.plan.state, PlanState.ARCHIVED)

    def test_available_transitions_match_the_role(self):
        with tenant_scope(self.data["school"].pk):
            self.assertEqual(workflow.transitions_for(self.data["teacher"], self.plan), ["submit"])
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
            head_actions = workflow.transitions_for(self.data["head"], self.plan)
        self.assertIn("approve", head_actions)
        self.assertIn("return", head_actions)
        self.assertNotIn("submit", head_actions)


class PdfTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()

    def test_lesson_plan_renders_a_single_a4_page(self):
        plan = self.make_lesson_plan(self.data)
        content, code = pdf.render_lesson_plan(plan)
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(content.count(b"/Type /Page\n"), 1)
        self.assertEqual(len(code), 10)

    def test_work_plan_renders_three_landscape_pages(self):
        with tenant_scope(self.data["school"].pk):
            plan = services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
            )
        content, _ = pdf.render_work_plan(plan)
        self.assertEqual(content.count(b"/Type /Page\n"), 3)

    def test_rendering_is_refused_without_an_approved_clean_master(self):
        plan = self.make_lesson_plan(self.data)
        version = plan.template_version
        version.clean_master_approved = False
        # Bypass the model lock deliberately to simulate a misconfigured version.
        type(version).all_objects.filter(pk=version.pk).update(clean_master_approved=False)
        plan.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            pdf.render_lesson_plan(plan)
        self.assertIn("clean master", str(ctx.exception))

    def test_generated_document_records_a_checksum(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.data["school"].pk):
            content, file_name, document = pdf.generate_document(plan)
        self.assertTrue(file_name.endswith(".pdf"))
        self.assertEqual(len(document.checksum), 64)
        self.assertEqual(document.byte_size, len(content))
        self.assertEqual(GeneratedDocument.all_objects.count(), 1)

    def test_same_revision_renders_identical_bytes(self):
        """8.6: the same saved version produces materially identical output."""
        plan = self.make_lesson_plan(self.data)
        first, _ = pdf.render_lesson_plan(plan)
        second, _ = pdf.render_lesson_plan(plan)
        self.assertEqual(first, second)

    def test_verification_code_changes_with_revision(self):
        plan = self.make_lesson_plan(self.data)
        first = pdf.verification_code(plan, 1)
        second = pdf.verification_code(plan, 2)
        self.assertNotEqual(first, second)

    def test_long_text_does_not_break_rendering(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.data["school"].pk):
            services.save_lesson_plan(
                membership=self.data["teacher"],
                plan=plan,
                main_teaching_activity="Extremely long content. " * 400,
                notes_remarks="Special characters: é ü ñ — “quotes” & <tags>",
            )
        content, _ = pdf.render_lesson_plan(plan)
        self.assertTrue(content.startswith(b"%PDF"))


class PlanScopeTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()

    def test_teacher_sees_only_their_own_plans(self):
        self.make_lesson_plan(self.data)
        other_user = User.objects.create_user(
            "other@lis.example", "StrongPass!246", full_name="Other Teacher"
        )
        other = Membership.objects.create(
            school=self.data["school"], user=other_user, role=Membership.Role.TEACHER
        )
        with tenant_scope(self.data["school"].pk):
            self.assertEqual(services.visible_plans(self.data["teacher"], LessonPlan).count(), 1)
            self.assertEqual(services.visible_plans(other, LessonPlan).count(), 0)
            self.assertEqual(services.visible_plans(self.data["head"], LessonPlan).count(), 1)

    def test_review_queue_excludes_own_plans(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.data["school"].pk):
            workflow.submit(membership=self.data["teacher"], plan=plan)
            self.assertEqual(services.review_queue(self.data["head"], LessonPlan).count(), 1)
            self.assertEqual(services.review_queue(self.data["teacher"], LessonPlan).count(), 0)

    def test_plans_are_invisible_across_schools(self):
        self.make_lesson_plan(self.data)
        other = self.build("Beta Cambridge", "BETA")
        with tenant_scope(other["school"].pk):
            self.assertEqual(LessonPlan.objects.count(), 0)
            self.assertEqual(WorkPlan.objects.count(), 0)

    def test_reviewer_cannot_touch_another_schools_plan(self):
        plan = self.make_lesson_plan(self.data)
        other = self.build("Beta Cambridge", "BETA")
        with tenant_scope(other["school"].pk), self.assertRaises(PermissionDenied):
            workflow.assert_can_review(other["head"], plan)
