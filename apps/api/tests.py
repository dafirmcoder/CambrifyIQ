from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.schools.models import Membership, School, SchoolClass, Subject, TeacherAssignment

User = get_user_model()


class ApiAccessTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            "teacher@example.com", "StrongPass!246", full_name="Teacher One"
        )
        self.alpha = School.objects.create(name="Alpha", slug="alpha", code="ALPHA")
        self.beta = School.objects.create(name="Beta", slug="beta", code="BETA")
        Membership.objects.create(
            school=self.alpha, user=self.teacher, role=Membership.Role.TEACHER, is_primary=True
        )
        Membership.objects.create(school=self.beta, user=self.teacher, role=Membership.Role.TEACHER)
        alpha_subject = Subject.all_objects.create(school=self.alpha, name="Science", code="SCI")
        beta_subject = Subject.all_objects.create(school=self.beta, name="Physics", code="PHY")
        alpha_class = SchoolClass.all_objects.create(school=self.alpha, name="Year 8")
        beta_class = SchoolClass.all_objects.create(school=self.beta, name="Year 9")
        TeacherAssignment.all_objects.create(
            school=self.alpha,
            teacher=self.teacher,
            subject=alpha_subject,
            school_class=alpha_class,
            effective_from=date.today(),
        )
        TeacherAssignment.all_objects.create(
            school=self.beta,
            teacher=self.teacher,
            subject=beta_subject,
            school_class=beta_class,
            effective_from=date.today(),
        )

    def test_me_requires_authentication(self):
        response = self.client.get(reverse("api:me"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_assignments_are_limited_to_active_school(self):
        self.client.force_login(self.teacher)
        session = self.client.session
        session["active_school_id"] = str(self.alpha.pk)
        session.save()
        response = self.client.get(reverse("api:assignments"))
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"]["code"], "SCI")

    def test_login_endpoint_creates_session(self):
        response = self.client.post(
            reverse("api:login"),
            {"email": "TEACHER@example.com", "password": "StrongPass!246"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], "teacher@example.com")
        self.assertIn("_auth_user_id", self.client.session)
