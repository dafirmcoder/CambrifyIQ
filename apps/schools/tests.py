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


# ── Part 1 test helpers ───────────────────────────────────────────────────────


def make_academic_year(school, name="2026/2027", starts_on=None, ends_on=None, is_current=False):
    from apps.schools.models import AcademicYear

    return AcademicYear.all_objects.create(
        school=school,
        name=name,
        starts_on=starts_on or date(2026, 9, 1),
        ends_on=ends_on or date(2027, 7, 31),
        is_current=is_current,
    )


def make_term(school, academic_year, name="Term 1", sequence=1, starts_on=None, ends_on=None):
    from apps.schools.models import Term

    return Term.all_objects.create(
        school=school,
        academic_year=academic_year,
        name=name,
        sequence=sequence,
        starts_on=starts_on or date(2026, 9, 1),
        ends_on=ends_on or date(2026, 12, 20),
    )


def make_calendar_week(school, term, sequence=1, starts_on=None, ends_on=None):
    from apps.schools.models import CalendarWeek

    return CalendarWeek.all_objects.create(
        school=school,
        term=term,
        sequence=sequence,
        starts_on=starts_on or date(2026, 9, 1),
        ends_on=ends_on or date(2026, 9, 7),
        month_label="SEPTEMBER",
    )


# ── Academic year tests ───────────────────────────────────────────────────────


class AcademicYearTests(TestCase):
    def setUp(self):
        self.school, self.director, self.director_membership = make_school(
            "Year School", "yr-director@example.com"
        )
        self.client.force_login(self.director)
        self.client.session["active_school_id"] = str(self.school.pk)

    def _session(self):
        """Force the session to be saved with the school_id set."""
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_create_academic_year(self):
        self._session()
        response = self.client.post(
            reverse("schools:academic_years"),
            {
                "name": "2026/2027",
                "starts_on": "2026-09-01",
                "ends_on": "2027-07-31",
                "is_current": "on",
            },
        )
        self.assertRedirects(response, reverse("schools:academic_years"))
        from apps.schools.models import AcademicYear

        self.assertTrue(
            AcademicYear.all_objects.filter(school=self.school, name="2026/2027").exists()
        )

    def test_is_current_only_one_per_school(self):
        """Setting is_current on a second year must clear the first."""
        from apps.schools.models import AcademicYear

        y1 = make_academic_year(self.school, name="2025/2026", is_current=True)
        self._session()
        self.client.post(
            reverse("schools:academic_years"),
            {
                "name": "2026/2027",
                "starts_on": "2026-09-01",
                "ends_on": "2027-07-31",
                "is_current": "on",
            },
        )
        y1.refresh_from_db()
        self.assertFalse(y1.is_current)
        y2 = AcademicYear.all_objects.get(school=self.school, name="2026/2027")
        self.assertTrue(y2.is_current)

    def test_overlap_rejected(self):
        make_academic_year(self.school, name="2026/2027")
        self._session()
        response = self.client.post(
            reverse("schools:academic_years"),
            {
                "name": "2026/2028",
                "starts_on": "2027-01-01",
                "ends_on": "2028-07-31",
                "is_current": "",
            },
        )
        # Should re-render with form error, not redirect
        self.assertEqual(response.status_code, 200)

    def test_delete_blocked_when_terms_exist(self):
        year = make_academic_year(self.school)
        make_term(self.school, year)
        self._session()
        response = self.client.post(reverse("schools:delete_academic_year", args=[year.pk]))
        self.assertRedirects(response, reverse("schools:academic_years"))
        from apps.schools.models import AcademicYear

        self.assertTrue(AcademicYear.all_objects.filter(pk=year.pk).exists())

    def test_teacher_cannot_access_academic_years(self):
        teacher = User.objects.create_user(
            "yr-teacher@example.com", "StrongPass!246", full_name="T One"
        )
        Membership.objects.create(school=self.school, user=teacher, role=Membership.Role.TEACHER)
        self.client.force_login(teacher)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(reverse("schools:academic_years"))
        self.assertEqual(response.status_code, 403)

    def test_cross_tenant_year_not_editable(self):
        other_school, _, _ = make_school("Other School", "other-yr@example.com")
        other_year = make_academic_year(other_school, name="Other Year")
        self._session()
        response = self.client.get(reverse("schools:edit_academic_year", args=[other_year.pk]))
        self.assertEqual(response.status_code, 404)


# ── Term tests ────────────────────────────────────────────────────────────────


class TermTests(TestCase):
    def setUp(self):
        self.school, self.director, _ = make_school("Term School", "term-dir@example.com")
        self.year = make_academic_year(self.school)
        self.client.force_login(self.director)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_create_term_inside_year(self):
        response = self.client.post(
            reverse("schools:terms", args=[self.year.pk]),
            {
                "name": "Term 1",
                "sequence": 1,
                "starts_on": "2026-09-01",
                "ends_on": "2026-12-20",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("schools:terms", args=[self.year.pk]))
        from apps.schools.models import Term

        self.assertTrue(Term.all_objects.filter(school=self.school, name="Term 1").exists())

    def test_term_outside_year_rejected(self):
        response = self.client.post(
            reverse("schools:terms", args=[self.year.pk]),
            {
                "name": "Bad Term",
                "sequence": 1,
                "starts_on": "2025-01-01",
                "ends_on": "2025-06-30",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors

    def test_overlapping_sibling_terms_rejected(self):
        make_term(
            self.school,
            self.year,
            name="T1",
            sequence=1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 12, 20),
        )
        response = self.client.post(
            reverse("schools:terms", args=[self.year.pk]),
            {
                "name": "T2",
                "sequence": 2,
                "starts_on": "2026-11-01",  # overlaps T1
                "ends_on": "2027-03-31",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_delete_blocked_when_weeks_exist(self):
        term = make_term(self.school, self.year)
        make_calendar_week(self.school, term)
        response = self.client.post(reverse("schools:delete_term", args=[self.year.pk, term.pk]))
        self.assertRedirects(response, reverse("schools:terms", args=[self.year.pk]))
        from apps.schools.models import Term

        self.assertTrue(Term.all_objects.filter(pk=term.pk).exists())

    def test_cross_tenant_term_not_accessible(self):
        other_school, _, _ = make_school("Other T", "other-t@example.com")
        other_year = make_academic_year(other_school, name="Other Y")
        other_term = make_term(other_school, other_year)
        response = self.client.get(
            reverse("schools:edit_term", args=[other_year.pk, other_term.pk])
        )
        self.assertEqual(response.status_code, 404)


# ── Calendar week tests ───────────────────────────────────────────────────────


class CalendarWeekTests(TestCase):
    def setUp(self):
        self.school, self.director, _ = make_school("Cal School", "cal-dir@example.com")
        self.year = make_academic_year(self.school)
        self.term = make_term(self.school, self.year)
        self.client.force_login(self.director)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_generate_preview_renders(self):
        response = self.client.post(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk]),
            {"action": "generate_preview", "gen-week_start_day": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Preview", response.content)

    def test_generate_weeks_creates_rows(self):
        from apps.schools.models import CalendarWeek

        self.client.post(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk]),
            {"action": "confirm_generate", "gen-week_start_day": "0"},
        )
        count = CalendarWeek.all_objects.filter(term=self.term).count()
        self.assertGreater(count, 0)

    def test_generate_is_idempotent(self):
        from apps.schools.models import CalendarWeek

        # Generate twice — should not duplicate
        for _ in range(2):
            self.client.post(
                reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk]),
                {"action": "confirm_generate", "gen-week_start_day": "0"},
            )
        count = CalendarWeek.all_objects.filter(term=self.term).count()
        # Second run shouldn't create extras — same count as first
        self.assertGreater(count, 0)
        self.assertLessEqual(count, 20)

    def test_delete_week_protected_when_in_use(self):
        """Deleting a CalendarWeek that is referenced by a WorkPlanWeek raises ProtectedError,
        which the view catches and shows as an error message."""
        week = make_calendar_week(self.school, self.term)
        # Create a fake WorkPlanWeek reference at DB level to trigger the PROTECT
        from apps.curriculum.models import CurriculumFramework, SchemeOfWork
        from apps.planning.models import (
            PlanningTemplate,
            TemplateVersion,
            WorkPlan,
            WorkPlanWeek,
        )

        framework = CurriculumFramework.objects.create(
            code="CAMB-CAL", name="Cambridge Calendar Test"
        )
        scheme = SchemeOfWork.objects.create(
            framework=framework,
            subject_code="MATH",
            subject_name="Mathematics",
            year_group="Year 9",
            title="Maths Y9",
            published_on=date(2026, 1, 1),
        )
        subj = Subject.all_objects.create(
            school=self.school, name="Maths", code="MATH", cambridge_code="MATH"
        )
        sc = SchoolClass.all_objects.create(school=self.school, name="9A", year_group="Year 9")
        teacher = User.objects.create_user(
            "cal-teacher@example.com", "StrongPass!246", full_name="Cal Teacher"
        )
        Membership.objects.create(school=self.school, user=teacher, role=Membership.Role.TEACHER)
        assignment = TeacherAssignment.all_objects.create(
            school=self.school,
            teacher=teacher,
            subject=subj,
            school_class=sc,
            effective_from=date(2026, 9, 1),
        )
        template = PlanningTemplate.all_objects.create(
            school=self.school,
            template_type=PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN,
            name="SWP",
        )
        tv = TemplateVersion.all_objects.create(
            school=self.school,
            template=template,
            version=1,
            status=TemplateVersion.Status.PUBLISHED,
            effective_from=date(2026, 1, 1),
        )
        wp = WorkPlan.all_objects.create(
            school=self.school,
            assignment=assignment,
            academic_year=self.year,
            term=self.term,
            scheme=scheme,
            template_version=tv,
            author=teacher,
        )
        WorkPlanWeek.all_objects.create(
            school=self.school,
            work_plan=wp,
            calendar_week=week,
            sequence=1,
            week_label=week.label,
        )

        self.client.post(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk]),
            {"action": "delete_week", "week_id": str(week.pk)},
        )
        # Week should still exist
        from apps.schools.models import CalendarWeek

        self.assertTrue(CalendarWeek.all_objects.filter(pk=week.pk).exists())

    def test_coordinator_can_view_but_not_generate(self):
        coord = User.objects.create_user(
            "cal-coord@example.com", "StrongPass!246", full_name="Coord One"
        )
        Membership.objects.create(school=self.school, user=coord, role=Membership.Role.COORDINATOR)
        self.client.force_login(coord)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        # GET should work
        response = self.client.get(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk])
        )
        self.assertEqual(response.status_code, 200)
        # POST generate should be 403
        response = self.client.post(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk]),
            {"action": "confirm_generate", "gen-week_start_day": "0"},
        )
        self.assertEqual(response.status_code, 403)

    def test_teacher_gets_403_on_calendar(self):
        teacher = User.objects.create_user("cal-t@example.com", "StrongPass!246", full_name="Cal T")
        Membership.objects.create(school=self.school, user=teacher, role=Membership.Role.TEACHER)
        self.client.force_login(teacher)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(
            reverse("schools:calendar_weeks", args=[self.year.pk, self.term.pk])
        )
        self.assertEqual(response.status_code, 403)


# ── Subject tests ─────────────────────────────────────────────────────────────


class SubjectTests(TestCase):
    def setUp(self):
        self.school, self.director, _ = make_school("Subj School", "subj-dir@example.com")
        self.client.force_login(self.director)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_create_subject(self):
        response = self.client.post(
            reverse("schools:subjects"),
            {"name": "Mathematics", "code": "MATH", "cambridge_code": "", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("schools:subjects"))
        self.assertTrue(Subject.all_objects.filter(school=self.school, code="MATH").exists())

    def test_mapping_health_unmapped(self):
        """A subject with no cambridge_code should show 'not mapped' in the list."""
        Subject.all_objects.create(school=self.school, name="Art", code="ART")
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(reverse("schools:subjects"))
        self.assertContains(response, "Not mapped")

    def test_cross_tenant_subject_not_editable(self):
        other_school, _, _ = make_school("Other S", "other-s@example.com")
        other_subj = Subject.all_objects.create(school=other_school, name="Science", code="SCI")
        response = self.client.get(reverse("schools:edit_subject", args=[other_subj.pk]))
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_access_subjects(self):
        teacher = User.objects.create_user("subj-t@example.com", "StrongPass!246", full_name="S T")
        Membership.objects.create(school=self.school, user=teacher, role=Membership.Role.TEACHER)
        self.client.force_login(teacher)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(reverse("schools:subjects"))
        self.assertEqual(response.status_code, 403)

    def test_coordinator_can_create_subject(self):
        coord = User.objects.create_user(
            "subj-coord@example.com", "StrongPass!246", full_name="C One"
        )
        Membership.objects.create(school=self.school, user=coord, role=Membership.Role.COORDINATOR)
        self.client.force_login(coord)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.post(
            reverse("schools:subjects"),
            {"name": "Physics", "code": "PHY", "cambridge_code": "", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("schools:subjects"))
        self.assertTrue(Subject.all_objects.filter(school=self.school, code="PHY").exists())

    def test_coordinator_cannot_delete_subject(self):
        coord = User.objects.create_user(
            "subj-coord2@example.com", "StrongPass!246", full_name="C Two"
        )
        Membership.objects.create(school=self.school, user=coord, role=Membership.Role.COORDINATOR)
        subj = Subject.all_objects.create(school=self.school, name="Chemistry", code="CHEM")
        self.client.force_login(coord)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.post(reverse("schools:delete_subject", args=[subj.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Subject.all_objects.filter(pk=subj.pk).exists())


# ── School class tests ────────────────────────────────────────────────────────


class SchoolClassTests(TestCase):
    def setUp(self):
        self.school, self.director, _ = make_school("Class School", "cls-dir@example.com")
        self.client.force_login(self.director)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_create_class(self):
        response = self.client.post(
            reverse("schools:school_classes"),
            {
                "name": "9A",
                "year_group": "Year 9",
                "boys_count": 15,
                "girls_count": 12,
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("schools:school_classes"))
        self.assertTrue(SchoolClass.all_objects.filter(school=self.school, name="9A").exists())

    def test_cross_tenant_class_not_editable(self):
        other_school, _, _ = make_school("Other C", "other-c@example.com")
        other_class = SchoolClass.all_objects.create(school=other_school, name="9B")
        response = self.client.get(reverse("schools:edit_school_class", args=[other_class.pk]))
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_access_classes(self):
        teacher = User.objects.create_user("cls-t@example.com", "StrongPass!246", full_name="C T")
        Membership.objects.create(school=self.school, user=teacher, role=Membership.Role.TEACHER)
        self.client.force_login(teacher)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(reverse("schools:school_classes"))
        self.assertEqual(response.status_code, 403)


# ── Teaching assignment tests ─────────────────────────────────────────────────


class TeacherAssignmentViewTests(TestCase):
    def setUp(self):
        self.school, self.director, _ = make_school("Assign School", "asgn-dir@example.com")
        self.teacher_user = User.objects.create_user(
            "asgn-teacher@example.com", "StrongPass!246", full_name="Assign Teacher"
        )
        Membership.objects.create(
            school=self.school, user=self.teacher_user, role=Membership.Role.TEACHER
        )
        self.subject = Subject.all_objects.create(school=self.school, name="Physics", code="PHY")
        self.school_class = SchoolClass.all_objects.create(school=self.school, name="10B")
        self.client.force_login(self.director)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()

    def test_create_assignment(self):
        response = self.client.post(
            reverse("schools:create_assignment"),
            {
                "teacher": str(self.teacher_user.pk),
                "subject": str(self.subject.pk),
                "school_class": str(self.school_class.pk),
                "effective_from": "2026-09-01",
                "effective_until": "",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("schools:teaching_assignments"))
        self.assertTrue(
            TeacherAssignment.all_objects.filter(
                school=self.school, teacher=self.teacher_user
            ).exists()
        )

    def test_cross_tenant_assignment_not_accessible(self):
        other_school, _, _ = make_school("Other A", "other-a@example.com")
        other_teacher = User.objects.create_user(
            "other-at@example.com", "StrongPass!246", full_name="Other T"
        )
        Membership.objects.create(
            school=other_school, user=other_teacher, role=Membership.Role.TEACHER
        )
        other_subj = Subject.all_objects.create(school=other_school, name="Bio", code="BIO")
        other_cls = SchoolClass.all_objects.create(school=other_school, name="11A")
        other_assign = TeacherAssignment.all_objects.create(
            school=other_school,
            teacher=other_teacher,
            subject=other_subj,
            school_class=other_cls,
            effective_from=date.today(),
        )
        response = self.client.get(reverse("schools:edit_assignment", args=[other_assign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_create_assignment(self):
        self.client.force_login(self.teacher_user)
        session = self.client.session
        session["active_school_id"] = str(self.school.pk)
        session.save()
        response = self.client.get(reverse("schools:create_assignment"))
        self.assertEqual(response.status_code, 403)
