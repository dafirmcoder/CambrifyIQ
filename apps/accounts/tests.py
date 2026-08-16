from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase


class EmailAuthenticationTests(TestCase):
    def test_email_authentication_is_case_insensitive(self):
        get_user_model().objects.create_user(
            email="leader@example.com", full_name="School Leader", password="StrongPass!246"
        )
        user = authenticate(username="LEADER@example.com", password="StrongPass!246")
        self.assertIsNotNone(user)
        self.assertEqual(user.email, "leader@example.com")
