"""Template lockdown tests — CAMS plan sections 8.1, 8.2, 8.7 and 8.8."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.core.tenant import tenant_scope
from apps.planning import services
from apps.planning.models import (
    ControlType,
    FieldKind,
    PlanType,
    TemplateField,
    TemplateFieldOption,
    TemplateVersion,
)
from apps.planning.register import (
    LESSON_PLAN_SOURCE,
    WORK_PLAN_PAGE_LAYOUT,
    WORK_PLAN_SPECIAL_EVENTS,
    counts_by_kind,
)
from apps.schools.models import AuditLog, Membership, School

User = get_user_model()

CLEAN_MASTER_SHA = "a" * 64


def make_school(name="Leera International", code="LIS"):
    director = User.objects.create_user(
        f"director@{code.lower()}.example", "StrongPass!246", full_name=f"{name} Director"
    )
    school = School.objects.create(name=name, slug=code.lower(), code=code, created_by=director)
    membership = Membership.objects.create(
        school=school, user=director, role=Membership.Role.DIRECTOR, is_primary=True
    )
    return school, director, membership


def make_member(school, role, email):
    user = User.objects.create_user(email, "StrongPass!246", full_name=email.split("@")[0])
    return Membership.objects.create(school=school, user=user, role=role)


class RegisterTests(TestCase):
    """The verified annotation register must match the approved plan exactly."""

    def test_lesson_plan_register_matches_verified_counts(self):
        totals = counts_by_kind(PlanType.LESSON_PLAN)
        self.assertEqual(totals[FieldKind.RED], 4)
        self.assertEqual(totals[FieldKind.BLUE], 3)
        self.assertEqual(totals[FieldKind.SYSTEM], 5)

    def test_lesson_plan_source_checksum_is_recorded(self):
        self.assertEqual(len(LESSON_PLAN_SOURCE["sha256"]), 64)
        self.assertEqual(LESSON_PLAN_SOURCE["pages"], 1)

    def test_work_plan_layout_covers_seventeen_weeks(self):
        weeks = [week for page in WORK_PLAN_PAGE_LAYOUT.values() for week in page["weeks"]]
        self.assertEqual(weeks, list(range(1, 18)))
        self.assertEqual(len(WORK_PLAN_PAGE_LAYOUT), 3)

    def test_special_events_are_preserved_for_weeks_15_to_17(self):
        self.assertEqual(
            WORK_PLAN_SPECIAL_EVENTS,
            {
                15: "Revision Week",
                16: "Semester Assessments",
                17: "End of First Semester & PTC",
            },
        )


class DraftSeedingTests(TestCase):
    def setUp(self):
        self.school, self.director, self.membership = make_school()

    def test_draft_seeds_every_declared_field(self):
        with tenant_scope(self.school.pk):
            version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )
        field_ids = set(version.field_map().values_list("field_id", flat=True))
        self.assertEqual(
            field_ids,
            {
                "LP-D01",
                "LP-D02",
                "LP-D03",
                "LP-D04",
                "LP-T01",
                "LP-T02",
                "LP-T03",
                "LP-S01",
                "LP-S02",
                "LP-S03",
                "LP-S04",
                "LP-S05",
            },
        )

    def test_boys_and_girls_remain_two_separate_field_ids(self):
        """8.1: the touching attendance circles stay two IDs."""
        with tenant_scope(self.school.pk):
            version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )
        boys = version.field_map().get(field_id="LP-D02")
        girls = version.field_map().get(field_id="LP-D03")
        self.assertNotEqual(boys.pk, girls.pk)
        self.assertNotEqual(boys.box, girls.box)
        for field in (boys, girls):
            self.assertEqual(field.control, ControlType.INTEGER_PICKER)
            self.assertEqual(field.min_value, 0)

    def test_measured_boxes_are_stored_for_annotated_fields(self):
        with tenant_scope(self.school.pk):
            version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )
        activity = version.field_map().get(field_id="LP-T01")
        self.assertEqual(activity.box, (66.2, 407.7, 530.0, 501.0))

    def test_resource_prompts_are_seeded(self):
        with tenant_scope(self.school.pk):
            services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )
            labels = list(
                TemplateFieldOption.objects.filter(field__field_id="LP-S05")
                .order_by("sequence")
                .values_list("label", flat=True)
            )
        self.assertIn("Projector", labels)
        self.assertEqual(len(labels), 6)

    def test_work_plan_draft_uses_three_landscape_pages(self):
        with tenant_scope(self.school.pk):
            version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.WORK_PLAN
            )
        self.assertEqual(version.page_count, 3)
        self.assertEqual(float(version.page_width_pt), 792.0)
        self.assertEqual(float(version.page_height_pt), 612.0)

    def test_teacher_cannot_propose_a_template_version(self):
        teacher = make_member(self.school, Membership.Role.TEACHER, "teacher@lis.example")
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            services.create_draft_version(membership=teacher, plan_type=PlanType.LESSON_PLAN)


class AnnotationRuleTests(TestCase):
    """8.1 locked annotation rules are enforced at the model layer."""

    def setUp(self):
        self.school, _, self.membership = make_school()
        with tenant_scope(self.school.pk):
            self.version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN, seed_register=False
            )

    def _field(self, **kwargs):
        defaults = {
            "school": self.school,
            "template_version": self.version,
            "field_id": "LP-X01",
            "label": "Test",
            "page": 1,
        }
        return TemplateField(**{**defaults, **kwargs})

    def test_red_field_cannot_be_free_text(self):
        field = self._field(kind=FieldKind.RED, control=ControlType.TEXTAREA)
        with self.assertRaises(ValidationError) as ctx:
            field.full_clean()
        self.assertIn("control", ctx.exception.message_dict)

    def test_red_field_requires_an_option_source(self):
        field = self._field(kind=FieldKind.RED, control=ControlType.SELECT)
        with self.assertRaises(ValidationError) as ctx:
            field.full_clean()
        self.assertIn("option_source", ctx.exception.message_dict)

    def test_blue_field_cannot_be_a_picker(self):
        field = self._field(kind=FieldKind.BLUE, control=ControlType.MULTI_SELECT)
        with self.assertRaises(ValidationError) as ctx:
            field.full_clean()
        self.assertIn("control", ctx.exception.message_dict)

    def test_blue_field_cannot_be_readonly(self):
        field = self._field(kind=FieldKind.BLUE, control=ControlType.TEXTAREA, is_readonly=True)
        with self.assertRaises(ValidationError) as ctx:
            field.full_clean()
        self.assertIn("is_readonly", ctx.exception.message_dict)

    def test_box_needs_positive_area(self):
        field = self._field(
            kind=FieldKind.BLUE,
            control=ControlType.TEXTAREA,
            box_x1=100,
            box_y1=100,
            box_x2=90,
            box_y2=120,
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_options_cannot_attach_to_a_free_text_field(self):
        field = self._field(kind=FieldKind.BLUE, control=ControlType.TEXTAREA)
        field.save()
        option = TemplateFieldOption(school=self.school, field=field, value="x", label="X")
        with self.assertRaises(ValidationError) as ctx:
            option.full_clean()
        self.assertIn("field", ctx.exception.message_dict)


class LockingProcedureTests(TestCase):
    """8.7 locking procedure and 8.8 acceptance criteria."""

    def setUp(self):
        self.school, self.director, self.membership = make_school()
        with tenant_scope(self.school.pk):
            self.version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )

    def _approve_clean_master(self):
        with tenant_scope(self.school.pk):
            services.record_clean_master(
                membership=self.membership,
                version=self.version,
                filename="LESSON_PLAN_CLEAN.pdf",
                checksum=CLEAN_MASTER_SHA,
                approved=True,
            )

    def test_version_cannot_lock_without_an_approved_clean_master(self):
        blockers = services.validate_for_lock(self.version)
        self.assertTrue(any("clean master" in item for item in blockers))
        with tenant_scope(self.school.pk), self.assertRaises(ValidationError):
            services.approve_version(membership=self.membership, version=self.version)

    def test_annotated_source_is_never_renderable(self):
        """Section 2: the flattened raster with circles cannot back production output."""
        self.assertFalse(self.version.is_renderable)
        self.assertEqual(self.version.annotation_source_name, "TEMPLATE.pdf")

    def test_full_lock_and_publish_flow(self):
        self._approve_clean_master()
        with tenant_scope(self.school.pk):
            self.assertEqual(services.validate_for_lock(self.version), [])
            services.submit_for_review(membership=self.membership, version=self.version)
            services.approve_version(membership=self.membership, version=self.version)
            services.publish_version(membership=self.membership, version=self.version)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, TemplateVersion.Status.CURRENT)
        self.assertIsNotNone(self.version.approved_at)
        self.assertIsNotNone(self.version.published_at)
        self.assertEqual(self.version.approved_by_id, self.director.pk)

    def test_coordinator_cannot_approve(self):
        self._approve_clean_master()
        coordinator = make_member(self.school, Membership.Role.COORDINATOR, "coord@lis.example")
        with tenant_scope(self.school.pk), self.assertRaises(PermissionDenied):
            services.approve_version(membership=coordinator, version=self.version)

    def test_approved_version_is_immutable(self):
        self._approve_clean_master()
        with tenant_scope(self.school.pk):
            services.approve_version(membership=self.membership, version=self.version)
        self.version.refresh_from_db()
        self.version.notes = "tampered"
        with self.assertRaises(ValidationError):
            self.version.save()

    def test_fields_cannot_change_on_a_locked_version(self):
        self._approve_clean_master()
        with tenant_scope(self.school.pk):
            services.approve_version(membership=self.membership, version=self.version)
            field = self.version.field_map().get(field_id="LP-T01")
            field.is_required = False
            with self.assertRaises(ValidationError):
                field.save()

    def test_publishing_supersedes_the_previous_current_version(self):
        self._approve_clean_master()
        with tenant_scope(self.school.pk):
            services.approve_version(membership=self.membership, version=self.version)
            services.publish_version(membership=self.membership, version=self.version)

            second = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )
            services.record_clean_master(
                membership=self.membership,
                version=second,
                filename="LESSON_PLAN_CLEAN_V2.pdf",
                checksum="b" * 64,
                approved=True,
            )
            services.approve_version(membership=self.membership, version=second)
            services.publish_version(membership=self.membership, version=second)

        self.version.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.version.status, TemplateVersion.Status.SUPERSEDED)
        self.assertEqual(second.status, TemplateVersion.Status.CURRENT)
        self.assertEqual(second.version, 2)

    def test_missing_register_field_blocks_the_lock(self):
        with tenant_scope(self.school.pk):
            self.version.field_map().filter(field_id="LP-D04").delete()
        blockers = services.validate_for_lock(self.version)
        self.assertTrue(any("LP-D04" in item for item in blockers))

    def test_template_transitions_are_audited(self):
        self._approve_clean_master()
        with tenant_scope(self.school.pk):
            services.approve_version(membership=self.membership, version=self.version)
        actions = set(
            AuditLog.all_objects.filter(school=self.school).values_list("action", flat=True)
        )
        self.assertIn("template.version_drafted", actions)
        self.assertIn("template.clean_master_recorded", actions)
        self.assertIn("template.version_approved", actions)


class FieldMapPayloadTests(TestCase):
    def setUp(self):
        self.school, _, self.membership = make_school()
        with tenant_scope(self.school.pk):
            self.version = services.create_draft_version(
                membership=self.membership, plan_type=PlanType.LESSON_PLAN
            )

    def test_payload_exposes_every_field_with_its_box(self):
        payload = services.field_map_payload(self.version)
        self.assertEqual(len(payload["fields"]), 12)
        activity = next(f for f in payload["fields"] if f["field_id"] == "LP-T01")
        self.assertEqual(activity["kind"], FieldKind.BLUE)
        self.assertTrue(activity["required"])
        self.assertEqual(activity["box"], [66.2, 407.7, 530.0, 501.0])
        self.assertEqual(activity["overflow_policy"], "warn")

    def test_payload_reports_clean_master_state(self):
        payload = services.field_map_payload(self.version)
        self.assertFalse(payload["clean_master_approved"])


class TenantIsolationTests(TestCase):
    """12: no cross-school discovery of template definitions."""

    def test_templates_are_invisible_across_schools(self):
        school_a, _, membership_a = make_school("Alpha Cambridge", "ALPHA")
        school_b, _, _ = make_school("Beta Cambridge", "BETA")
        with tenant_scope(school_a.pk):
            services.create_draft_version(membership=membership_a, plan_type=PlanType.LESSON_PLAN)
        with tenant_scope(school_b.pk):
            self.assertEqual(TemplateVersion.objects.count(), 0)
            self.assertEqual(TemplateField.objects.count(), 0)

    def test_managers_fail_closed_without_tenant_context(self):
        school, _, membership = make_school("Gamma Cambridge", "GAMMA")
        with tenant_scope(school.pk):
            services.create_draft_version(membership=membership, plan_type=PlanType.LESSON_PLAN)
        self.assertEqual(TemplateVersion.objects.count(), 0)
