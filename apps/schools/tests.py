from datetime import date

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.tenant import tenant_scope
from apps.schools.models import (
    AuditLog,
    Invitation,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
)
from apps.schools.services import accept_invitation, invite_staff, register_school

User = get_user_model()


def make_school(name, owner_email):
    owner = User.objects.create_user(owner_email, "StrongPass!246", full_name=f"{name} Director")
    school = School.objects.create(
        name=name,
        slug=name.lower().replace(" ", "-"),
        code=owner_email.split("@")[0].upper(),
        created_by=owner,
    )
    membership = Membership.objects.create(
        school=school, user=owner, role=Membership.Role.DIRECTOR, is_primary=True
    )
    return school, owner, membership


class RegistrationTests(TestCase):
    def test_head_can_create_school_workspace(self):
        response = self.client.post(
            reverse("accounts:create_school"),
            {
                "full_name": "Amina Yusuf",
                "email": "amina@example.com",
                "password1": "Cambridge!Plan246",
                "password2": "Cambridge!Plan246",
                "school_name": "Bahari Cambridge School",
                "leadership_role": Membership.Role.HEAD,
                "accept_terms": "on",
            },
        )
        self.assertRedirects(response, reverse("dashboard:home"), fetch_redirect_response=False)
        school = School.objects.get(name="Bahari Cambridge School")
        member = Membership.objects.get(school=school, user__email="amina@example.com")
        self.assertEqual(member.role, Membership.Role.HEAD)
        self.assertTrue(member.is_primary)
        self.assertTrue(
            AuditLog.all_objects.filter(school=school, action="school.created").exists()
        )

    def test_registration_rolls_back_when_role_is_not_leadership(self):
        with self.assertRaises(ValidationError):
            register_school(
                full_name="Not Leader",
                email="teacher@example.com",
                password1="Cambridge!Plan246",
                school_name="Invalid School",
                leadership_role=Membership.Role.TEACHER,
            )
        self.assertFalse(User.objects.filter(email="teacher@example.com").exists())


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha, self.alpha_owner, _ = make_school("Alpha School", "alpha@example.com")
        self.beta, self.beta_owner, _ = make_school("Beta School", "beta@example.com")
        self.alpha_subject = Subject.all_objects.create(
            school=self.alpha, name="Science", code="SCI"
        )
        self.beta_subject = Subject.all_objects.create(school=self.beta, name="Physics", code="PHY")

    def test_scoped_manager_fails_closed_without_tenant(self):
        self.assertEqual(Subject.objects.count(), 0)

    def test_scoped_manager_returns_only_active_tenant_rows(self):
        with tenant_scope(self.alpha):
            self.assertEqual(list(Subject.objects.values_list("code", flat=True)), ["SCI"])
        with tenant_scope(self.beta):
            self.assertEqual(list(Subject.objects.values_list("code", flat=True)), ["PHY"])

    def test_active_school_switch_never_accepts_non_membership(self):
        self.client.force_login(self.alpha_owner)
        response = self.client.post(reverse("schools:switch", args=[self.beta.pk]))
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_open_user_management(self):
        teacher = User.objects.create_user(
            "teacher@example.com", "StrongPass!246", full_name="T. One"
        )
        Membership.objects.create(school=self.alpha, user=teacher, role=Membership.Role.TEACHER)
        self.client.force_login(teacher)
        response = self.client.get(reverse("schools:team"))
        self.assertEqual(response.status_code, 403)


class InvitationTests(TestCase):
    def setUp(self):
        self.school, self.director, self.director_membership = make_school(
            "Invite School", "director@example.com"
        )

    def test_invitation_stores_hash_and_accepts_new_member(self):
        with self.captureOnCommitCallbacks(execute=True):
            invitation, raw_token = invite_staff(
                actor_membership=self.director_membership,
                email="teacher@example.com",
                role=Membership.Role.TEACHER,
            )
        self.assertNotEqual(invitation.token_hash, raw_token)
        self.assertEqual(invitation.token_hash, Invitation.hash_token(raw_token))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(raw_token, mail.outbox[0].body)

        teacher = User.objects.create_user(
            "teacher@example.com", "StrongPass!246", full_name="Teacher One"
        )
        membership = accept_invitation(invitation=invitation, user=teacher)
        self.assertEqual(membership.school, self.school)
        self.assertEqual(membership.role, Membership.Role.TEACHER)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.ACCEPTED)

    def test_head_cannot_invite_director(self):
        head = User.objects.create_user("head@example.com", "StrongPass!246", full_name="Head One")
        head_membership = Membership.objects.create(
            school=self.school, user=head, role=Membership.Role.HEAD
        )
        with self.assertRaises(PermissionDenied):
            invite_staff(
                actor_membership=head_membership,
                email="newdirector@example.com",
                role=Membership.Role.DIRECTOR,
            )

    def test_invitation_rejects_wrong_signed_in_email(self):
        invitation, raw_token = invite_staff(
            actor_membership=self.director_membership,
            email="expected@example.com",
            role=Membership.Role.COORDINATOR,
        )
        wrong_user = User.objects.create_user(
            "wrong@example.com", "StrongPass!246", full_name="Wrong User"
        )
        self.client.force_login(wrong_user)
        response = self.client.post(reverse("schools:accept_invitation", args=[raw_token]))
        self.assertEqual(response.status_code, 403)


class AssignmentValidationTests(TestCase):
    def test_assignment_rejects_cross_school_subject(self):
        alpha, _, _ = make_school("Assignments A", "assign-a@example.com")
        beta, _, _ = make_school("Assignments B", "assign-b@example.com")
        teacher = User.objects.create_user("t@example.com", "StrongPass!246", full_name="Teacher")
        Membership.objects.create(school=alpha, user=teacher, role=Membership.Role.TEACHER)
        foreign_subject = Subject.all_objects.create(school=beta, name="Science", code="SCI")
        school_class = SchoolClass.all_objects.create(school=alpha, name="Year 8")
        assignment = TeacherAssignment(
            school=alpha,
            teacher=teacher,
            subject=foreign_subject,
            school_class=school_class,
            effective_from=date.today(),
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()
