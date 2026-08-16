"""Builder view and plan API tests (plan sections 12, 13, 14)."""

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.core.tenant import tenant_scope
from apps.plans import services, workflow
from apps.plans.models import PlanState
from apps.plans.tests import PlanFixture


class BuilderViewTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()
        self.school = self.data["school"]
        self.plan = self.make_lesson_plan(self.data)
        self.client.force_login(self.data["teacher"].user)

    def test_builder_renders_every_verified_field(self):
        response = self.client.get(reverse("plans:lesson_plan", args=[self.plan.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for field_id in ("LP-D01", "LP-D02", "LP-D03", "LP-D04", "LP-T01", "LP-T02", "LP-T03"):
            self.assertIn(field_id, body)

    def test_autosave_updates_and_returns_the_new_revision(self):
        response = self.client.post(
            reverse("plans:save_lesson_plan", args=[self.plan.pk]),
            {"notes_remarks": "Bring spare stopwatches.", "revision": self.plan.revision},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["revision"], self.plan.revision + 1)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes_remarks, "Bring spare stopwatches.")

    def test_stale_autosave_returns_409(self):
        self.client.post(
            reverse("plans:save_lesson_plan", args=[self.plan.pk]),
            {"notes_remarks": "First", "revision": self.plan.revision},
        )
        response = self.client.post(
            reverse("plans:save_lesson_plan", args=[self.plan.pk]),
            {"notes_remarks": "Second", "revision": self.plan.revision},
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["conflict"])

    def test_attendance_over_roster_returns_400(self):
        response = self.client.post(
            reverse("plans:save_lesson_plan", args=[self.plan.pk]),
            {"boys_present": 99, "girls_present": 2},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_another_teacher_cannot_open_the_plan(self):
        from django.contrib.auth import get_user_model

        from apps.schools.models import Membership

        User = get_user_model()
        other = User.objects.create_user(
            "other@lis.example", "StrongPass!246", full_name="Other Teacher"
        )
        Membership.objects.create(school=self.school, user=other, role=Membership.Role.TEACHER)
        self.client.force_login(other)
        response = self.client.get(reverse("plans:lesson_plan", args=[self.plan.pk]))
        self.assertEqual(response.status_code, 403)

    def test_cross_school_plan_is_not_found(self):
        other = self.build("Beta Cambridge", "BETA")
        self.client.force_login(other["teacher"].user)
        response = self.client.get(reverse("plans:lesson_plan", args=[self.plan.pk]))
        self.assertEqual(response.status_code, 404)

    def test_submit_then_approve_through_the_ui(self):
        self.client.post(reverse("plans:transition", args=["lesson", self.plan.pk, "submit"]))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.state, PlanState.SUBMITTED)

        self.client.force_login(self.data["head"].user)
        self.client.post(
            reverse("plans:transition", args=["lesson", self.plan.pk, "approve"]),
            {"comment": "Approved."},
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.state, PlanState.APPROVED)

    def test_return_without_comment_is_rejected(self):
        self.client.post(reverse("plans:transition", args=["lesson", self.plan.pk, "submit"]))
        self.client.force_login(self.data["head"].user)
        self.client.post(
            reverse("plans:transition", args=["lesson", self.plan.pk, "return"]),
            {"comment": ""},
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.state, PlanState.SUBMITTED)

    def test_pdf_download_is_served(self):
        response = self.client.get(reverse("plans:pdf", args=["lesson", self.plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_teacher_cannot_open_the_review_queue(self):
        response = self.client.get(reverse("plans:review_queue"))
        self.assertEqual(response.status_code, 403)

    def test_head_sees_the_review_queue(self):
        with tenant_scope(self.school.pk):
            workflow.submit(membership=self.data["teacher"], plan=self.plan)
        self.client.force_login(self.data["head"].user)
        response = self.client.get(reverse("plans:review_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data["teacher"].user.get_short_name())

    def test_work_plan_builder_lists_all_weeks(self):
        with tenant_scope(self.school.pk):
            plan = services.create_work_plan(
                membership=self.data["teacher"],
                assignment_id=self.data["assignment"].pk,
                term=self.data["term"],
            )
        response = self.client.get(reverse("plans:work_plan", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Week 17")
        self.assertContains(response, "Revision Week")


class PlanApiTests(TestCase, PlanFixture):
    def setUp(self):
        self.data = self.build()
        self.school = self.data["school"]
        self.plan = self.make_lesson_plan(self.data)
        self.client.force_login(self.data["teacher"].user)

    def test_list_returns_only_own_plans(self):
        response = self.client.get("/api/lesson-plans/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_detail_includes_issues_and_state(self):
        response = self.client.get(f"/api/lesson-plans/{self.plan.pk}/")
        body = response.json()
        self.assertEqual(body["state"], PlanState.DRAFT)
        self.assertEqual(body["attendance_total"], 22)
        self.assertEqual(body["issues"], [])

    def test_patch_saves_with_a_revision_token(self):
        response = self.client.patch(
            f"/api/lesson-plans/{self.plan.pk}/",
            data=json.dumps({"notes_remarks": "API edit", "revision": self.plan.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes_remarks, "API edit")

    def test_patch_with_stale_revision_returns_409(self):
        response = self.client.patch(
            f"/api/lesson-plans/{self.plan.pk}/",
            data=json.dumps({"notes_remarks": "Stale", "revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["conflict"])

    def test_workflow_action_endpoint(self):
        response = self.client.post(
            f"/api/lesson-plans/{self.plan.pk}/submit/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], PlanState.SUBMITTED)

    def test_teacher_cannot_approve_via_api(self):
        self.client.post(
            f"/api/lesson-plans/{self.plan.pk}/submit/",
            data=json.dumps({}),
            content_type="application/json",
        )
        response = self.client.post(
            f"/api/lesson-plans/{self.plan.pk}/approve/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_sync_endpoint_applies_a_batch(self):
        payload = {
            "operations": [
                {
                    "operation_id": "api-op-1",
                    "name": "lesson_plan.save",
                    "plan_id": str(self.plan.pk),
                    "base_revision": self.plan.revision,
                    "payload": {"notes_remarks": "From the queue"},
                }
            ]
        }
        response = self.client.post(
            "/api/sync/operations/", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["result"], "applied")

    def test_sync_reports_conflicts_with_207(self):
        payload = {
            "operations": [
                {
                    "operation_id": "api-op-2",
                    "name": "lesson_plan.save",
                    "plan_id": str(self.plan.pk),
                    "base_revision": 1,
                    "payload": {"notes_remarks": "Stale"},
                }
            ]
        }
        with tenant_scope(self.school.pk):
            services.save_lesson_plan(
                membership=self.data["teacher"], plan=self.plan, notes_remarks="Newer"
            )
        response = self.client.post(
            "/api/sync/operations/", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.json()["conflicts"], 1)

    def test_dashboard_endpoint_is_role_locked(self):
        allowed = self.client.get("/api/dashboard/teacher/")
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("summary", allowed.json())
        denied = self.client.get("/api/dashboard/head/")
        self.assertEqual(denied.status_code, 403)

    def test_api_pdf_endpoint(self):
        response = self.client.get(f"/api/lesson-plans/{self.plan.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_cross_school_plan_is_hidden_from_the_api(self):
        other = self.build("Beta Cambridge", "BETA")
        self.client.force_login(other["teacher"].user)
        response = self.client.get(f"/api/lesson-plans/{self.plan.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_create_lesson_plan_via_api(self):
        response = self.client.post(
            "/api/lesson-plans/",
            data=json.dumps(
                {
                    "assignment": str(self.data["assignment"].pk),
                    "lesson_date": date(2026, 10, 6).isoformat(),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["state"], PlanState.DRAFT)
