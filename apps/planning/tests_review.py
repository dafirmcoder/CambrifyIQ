from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.schools.models import Membership, School


class ReviewQueueTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Review School")
        self.coordinator = User.objects.create_user("coordinator@example.com", "password")
        self.teacher = User.objects.create_user("teacher@example.com", "password")

        Membership.objects.create(
            school=self.school, user=self.coordinator, role=Membership.Role.COORDINATOR
        )
        Membership.objects.create(
            school=self.school, user=self.teacher, role=Membership.Role.TEACHER
        )

    def test_teacher_cannot_access_review_queue(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("planning:review_queue"))
        self.assertEqual(response.status_code, 403)

    def test_coordinator_can_access_review_queue(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse("planning:review_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "planning/review_queue.html")
