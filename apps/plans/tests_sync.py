"""Offline sync and reporting tests (plan sections 7.6, 10.3)."""

from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.dashboard import reporting
from apps.plans import services, sync, workflow
from apps.plans.models import LessonPlan, PlanState, SyncOperation, WorkPlan
from apps.plans.tests import PlanFixture


class SyncTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()
        self.plan = self.make_lesson_plan(self.data, complete=False)
        self.school = self.data["school"]

    def operation(self, **overrides):
        base = {
            "operation_id": "op-1",
            "device_id": "device-a",
            "name": "lesson_plan.save",
            "plan_type": "lesson_plan",
            "plan_id": str(self.plan.pk),
            "base_revision": self.plan.revision,
            "payload": {"notes_remarks": "Written on the bus."},
        }
        base.update(overrides)
        return base

    def test_operation_applies_and_is_recorded(self):
        with tenant_scope(self.school.pk):
            result = sync.apply_operation(
                membership=self.data["teacher"], operation=self.operation()
            )
        self.assertEqual(result["result"], SyncOperation.Result.APPLIED)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes_remarks, "Written on the bus.")

    def test_replaying_the_same_operation_is_idempotent(self):
        with tenant_scope(self.school.pk):
            sync.apply_operation(membership=self.data["teacher"], operation=self.operation())
            repeat = sync.apply_operation(
                membership=self.data["teacher"],
                operation=self.operation(payload={"notes_remarks": "Different text."}),
            )
        self.assertEqual(repeat["result"], SyncOperation.Result.DUPLICATE)
        self.plan.refresh_from_db()
        # The replay must not overwrite with the second payload.
        self.assertEqual(self.plan.notes_remarks, "Written on the bus.")

    def test_stale_revision_is_reported_as_a_conflict(self):
        with tenant_scope(self.school.pk):
            services.save_lesson_plan(
                membership=self.data["teacher"], plan=self.plan, notes_remarks="Newer edit"
            )
            result = sync.apply_operation(
                membership=self.data["teacher"],
                operation=self.operation(operation_id="op-stale", base_revision=1),
            )
        self.assertEqual(result["result"], SyncOperation.Result.CONFLICT)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes_remarks, "Newer edit")

    def test_unknown_operation_is_rejected(self):
        with tenant_scope(self.school.pk):
            result = sync.apply_operation(
                membership=self.data["teacher"],
                operation=self.operation(operation_id="op-x", name="plan.delete_everything"),
            )
        self.assertEqual(result["result"], SyncOperation.Result.REJECTED)

    def test_operation_without_an_id_is_refused(self):
        from django.core.exceptions import ValidationError

        with tenant_scope(self.school.pk), self.assertRaises(ValidationError):
            sync.apply_operation(
                membership=self.data["teacher"], operation=self.operation(operation_id="")
            )

    def test_another_users_plan_is_rejected(self):
        other = self.build("Beta Cambridge", "BETA")
        with tenant_scope(other["school"].pk):
            result = sync.apply_operation(
                membership=other["teacher"],
                operation=self.operation(operation_id="op-cross"),
            )
        self.assertEqual(result["result"], SyncOperation.Result.REJECTED)

    def test_batch_returns_one_result_per_operation(self):
        with tenant_scope(self.school.pk):
            results = sync.apply_batch(
                membership=self.data["teacher"],
                operations=[
                    self.operation(operation_id="b1"),
                    self.operation(operation_id="b2", payload={"assessment_ideas": "Quiz"}),
                    self.operation(operation_id="b3", name="nope"),
                ],
            )
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["result"], SyncOperation.Result.APPLIED)
        self.assertEqual(results[2]["result"], SyncOperation.Result.REJECTED)

    def test_work_plan_row_operation(self):
        with tenant_scope(self.school.pk):
            work_plan = services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
            )
            row = services.week_rows_for(work_plan).first()
            result = sync.apply_operation(
                membership=self.data["teacher"],
                operation={
                    "operation_id": "row-1",
                    "name": "work_plan.save_row",
                    "plan_id": str(work_plan.pk),
                    "base_revision": work_plan.revision,
                    "payload": {"row_id": str(row.pk), "remarks": "Offline remark"},
                },
            )
        self.assertEqual(result["result"], SyncOperation.Result.APPLIED)
        row.refresh_from_db()
        self.assertEqual(row.remarks, "Offline remark")


class ReportingTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()
        self.school = self.data["school"]

    def test_teacher_summary_counts_states(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.school.pk):
            summary = reporting.teacher_summary(self.data["teacher"])
            self.assertEqual(summary["drafts"], 1)
            workflow.submit(membership=self.data["teacher"], plan=plan)
            workflow.return_for_changes(membership=self.data["head"], plan=plan, comment="Revise")
            summary = reporting.teacher_summary(self.data["teacher"])
        self.assertEqual(summary["returned"], 1)
        self.assertEqual(summary["assignments"], 1)

    def test_pending_counts_exclude_own_plans(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.school.pk):
            workflow.submit(membership=self.data["teacher"], plan=plan)
            head_view = reporting.pending_review_counts(self.data["head"])
            teacher_view = reporting.pending_review_counts(self.data["teacher"])
        self.assertEqual(head_view["lesson_plans"], 1)
        self.assertEqual(teacher_view["lesson_plans"], 0)

    def test_coverage_reflects_planned_objectives(self):
        with tenant_scope(self.school.pk):
            rows = reporting.coverage_by_assignment(self.data["teacher"])
            self.assertEqual(rows[0]["available"], 1)
            self.assertEqual(rows[0]["planned"], 0)
            self.assertEqual(rows[0]["percent"], 0)

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
            rows = reporting.coverage_by_assignment(self.data["teacher"])
        self.assertEqual(rows[0]["planned"], 1)
        self.assertEqual(rows[0]["percent"], 100)

    def test_completion_rate_and_turnaround(self):
        plan = self.make_lesson_plan(self.data)
        with tenant_scope(self.school.pk):
            self.assertEqual(reporting.completion_rate(self.data["head"]), 0)
            workflow.submit(membership=self.data["teacher"], plan=plan)
            workflow.approve(membership=self.data["head"], plan=plan)
            self.assertEqual(reporting.completion_rate(self.data["head"]), 100)
            self.assertIsNotNone(reporting.turnaround_days(self.data["head"]))

    def test_content_health_for_coordinators(self):
        with tenant_scope(self.school.pk):
            health = reporting.content_health(self.data["head"])
        self.assertEqual(health["published"], 1)
        self.assertEqual(health["objectives"], 1)

    def test_dashboard_context_is_role_shaped(self):
        with tenant_scope(self.school.pk):
            teacher_context = reporting.dashboard_context(self.data["teacher"])
            head_context = reporting.dashboard_context(self.data["head"])
        self.assertIn("summary", teacher_context)
        self.assertNotIn("pending", teacher_context)
        self.assertIn("pending", head_context)
        self.assertIn("completion", head_context)

    def test_reporting_is_tenant_scoped(self):
        self.make_lesson_plan(self.data)
        other = self.build("Beta Cambridge", "BETA")
        with tenant_scope(other["school"].pk):
            counts = reporting.plan_state_counts(other["head"], LessonPlan)
        self.assertEqual(counts[PlanState.DRAFT], 0)

    def test_state_counts_cover_work_plans(self):
        with tenant_scope(self.school.pk):
            services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
            )
            counts = reporting.plan_state_counts(self.data["head"], WorkPlan)
        self.assertEqual(counts[PlanState.DRAFT], 1)
