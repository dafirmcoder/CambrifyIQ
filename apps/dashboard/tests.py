from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.schools.models import Membership, School

User = get_user_model()


class DashboardTests(TestCase):
    def test_member_sees_role_specific_dashboard(self):
        user = User.objects.create_user(
            "head@example.com", "StrongPass!246", full_name="Amina Head"
        )
        school = School.objects.create(name="Dashboard School", slug="dashboard", code="DASH")
        Membership.objects.create(school=school, user=user, role=Membership.Role.HEAD)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Good day, Amina")
        self.assertContains(response, "Head of Cambridge")

    def test_authenticated_user_without_school_is_sent_to_onboarding(self):
        user = User.objects.create_user("solo@example.com", "StrongPass!246", full_name="Solo User")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(
            response, reverse("accounts:create_school"), fetch_redirect_response=False
        )

    def test_teacher_sees_plan_counts(self):
        user = User.objects.create_user(
            "teacher@example.com", "StrongPass!246", full_name="John Teacher"
        )
        school = School.objects.create(name="Dashboard School", slug="dashboard-2", code="DASH2")
        Membership.objects.create(school=school, user=user, role=Membership.Role.TEACHER)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft Plans")
        self.assertContains(response, "Returned Plans")
        self.assertContains(response, "Approved Plans")
        self.assertContains(response, "0")
