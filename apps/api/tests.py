"""Scoped API tests — plan sections 10.5, 12 and 14."""

from django.test import TestCase
from django.urls import reverse

from apps.core.tenant import tenant_scope
from apps.curriculum.tests import CurriculumFixture
from apps.planning import services as planning_services
from apps.planning.models import PlanType
from apps.schools.models import Membership

PASSWORD = "StrongPass!246"


class CurriculumAPITests(TestCase, CurriculumFixture):
    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")
        self.school = self.data["school"]
        self.teacher = self.add_teacher(self.data, "teacher@lis.example")
        self.stranger = self.add_teacher(self.data, "stranger@lis.example", assigned=False)

    def login(self, membership):
        self.client.force_login(membership.user)

    def test_assigned_teacher_lists_their_scheme(self):
        self.login(self.teacher)
        response = self.client.get(reverse("api:schemes"))
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "LIS-SCI-Y8-S1")
        self.assertEqual(results[0]["subject"]["code"], "SCI")

    def test_unassigned_teacher_lists_nothing(self):
        self.login(self.stranger)
        response = self.client.get(reverse("api:schemes"))
        self.assertEqual(response.json()["results"], [])

    def test_objective_tree_is_returned_for_an_assigned_scheme(self):
        self.login(self.teacher)
        response = self.client.get(reverse("api:scheme-objectives", args=[self.data["scheme"].pk]))
        self.assertEqual(response.status_code, 200)
        topics = response.json()["results"]
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["code"], "T1")
        self.assertEqual(topics[0]["subtopics"][0]["code"], "T1.1")
        codes = {item["code"] for item in topics[0]["objectives"]}
        self.assertEqual(codes, {"8Ps.01", "8Ps.02"})

    def test_direct_url_to_an_unassigned_scheme_is_denied(self):
        """12: a direct API attempt outside scope returns no protected record."""
        self.login(self.stranger)
        response = self.client.get(reverse("api:scheme-objectives", args=[self.data["scheme"].pk]))
        self.assertEqual(response.status_code, 403)

    def test_cross_school_scheme_is_denied(self):
        other = self.build_school("Beta Cambridge", "BETA")
        self.login(self.teacher)
        response = self.client.get(reverse("api:scheme-objectives", args=[other["scheme"].pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_access_is_rejected(self):
        response = self.client.get(reverse("api:schemes"))
        self.assertIn(response.status_code, (401, 403))


class TemplateAPITests(TestCase, CurriculumFixture):
    def setUp(self):
        self.data = self.build_school("Leera International", "LIS")
        self.school = self.data["school"]
        self.director = Membership.objects.get(school=self.school, role=Membership.Role.DIRECTOR)
        self.teacher = self.add_teacher(self.data, "teacher@lis.example")
        with tenant_scope(self.school.pk):
            self.version = planning_services.create_draft_version(
                membership=self.director, plan_type=PlanType.LESSON_PLAN
            )

    def publish(self):
        with tenant_scope(self.school.pk):
            planning_services.record_clean_master(
                membership=self.director,
                version=self.version,
                filename="clean.pdf",
                checksum="a" * 64,
                approved=True,
            )
            planning_services.approve_version(membership=self.director, version=self.version)
            planning_services.publish_version(membership=self.director, version=self.version)

    def test_unpublished_template_is_not_served(self):
        self.client.force_login(self.teacher.user)
        response = self.client.get(reverse("api:templates"), {"type": "lesson_plan"})
        self.assertEqual(response.status_code, 404)

    def test_published_template_is_served_to_a_teacher(self):
        self.publish()
        self.client.force_login(self.teacher.user)
        response = self.client.get(reverse("api:templates"), {"type": "lesson_plan"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["version"], 1)
        self.assertTrue(body["renderable"])

    def test_field_map_exposes_the_verified_register(self):
        self.publish()
        self.client.force_login(self.teacher.user)
        response = self.client.get(reverse("api:template-fields", args=[self.version.pk]))
        self.assertEqual(response.status_code, 200)
        fields = response.json()["fields"]
        self.assertEqual(len(fields), 12)
        red = [f for f in fields if f["kind"] == "red"]
        blue = [f for f in fields if f["kind"] == "blue"]
        self.assertEqual(len(red), 4)
        self.assertEqual(len(blue), 3)
        self.assertTrue(all(f["option_source"] for f in red))

    def test_unknown_template_type_is_rejected(self):
        self.client.force_login(self.teacher.user)
        response = self.client.get(reverse("api:templates"), {"type": "nonsense"})
        self.assertEqual(response.status_code, 400)

    def test_field_map_of_another_school_is_hidden(self):
        other = self.build_school("Beta Cambridge", "BETA")
        other_director = Membership.objects.get(
            school=other["school"], role=Membership.Role.DIRECTOR
        )
        with tenant_scope(other["school"].pk):
            other_version = planning_services.create_draft_version(
                membership=other_director, plan_type=PlanType.LESSON_PLAN
            )
        self.client.force_login(self.teacher.user)
        response = self.client.get(reverse("api:template-fields", args=[other_version.pk]))
        self.assertEqual(response.status_code, 404)
