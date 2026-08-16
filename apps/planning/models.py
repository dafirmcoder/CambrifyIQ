"""Immutable planning template definitions (Phase 0 — Template Lockdown).

Sections 8.1–8.8 of the CAMS plan turn the school's Lesson Plan and Semester
Work Plan layouts into versioned application templates. The models here store:

* ``PlanningTemplate``  — one per plan type, per school.
* ``TemplateVersion``   — immutable source assets, checksums and approval metadata.
* ``TemplateField``     — the RED/BLUE/system field map with PDF placement boxes.
* ``TemplateFieldOption`` — static option lists for controlled fields.

Locked annotation rules (8.1) are enforced in ``TemplateField.clean``:

* RED    = controlled picker; no uncontrolled typing.
* BLUE   = teacher-authored free text.
* System = fixed or read-only content populated from context.

Once a version leaves draft its definition is frozen; any source, field-map,
coordinate or visual change must create a new ``TemplateVersion``.
"""

import decimal
import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.schools.models import SchoolScopedManager


class PlanType(models.TextChoices):
    WORK_PLAN = "work_plan", "Semester Work Plan"
    LESSON_PLAN = "lesson_plan", "Lesson Plan"


class FieldKind(models.TextChoices):
    """Annotation classification from the approved source (8.1)."""

    RED = "red", "RED — controlled picker"
    BLUE = "blue", "BLUE — free text"
    SYSTEM = "system", "System — fixed or read-only"


class ControlType(models.TextChoices):
    CASCADING_SELECT = "cascading_select", "Cascading searchable dropdown"
    MULTI_SELECT = "multi_select", "Searchable multi-select"
    SELECT = "select", "Single select"
    INTEGER_PICKER = "integer_picker", "Bounded integer picker"
    TEXT = "text", "Single-line text"
    TEXTAREA = "textarea", "Multi-line text"
    DATE = "date", "Date"
    COMPUTED = "computed", "Computed read-only value"
    STATIC = "static", "Fixed template content"


#: Control types each annotation colour may legally use.
ALLOWED_CONTROLS = {
    FieldKind.RED: {
        ControlType.CASCADING_SELECT,
        ControlType.MULTI_SELECT,
        ControlType.SELECT,
        ControlType.INTEGER_PICKER,
    },
    FieldKind.BLUE: {ControlType.TEXT, ControlType.TEXTAREA},
    FieldKind.SYSTEM: {
        ControlType.STATIC,
        ControlType.COMPUTED,
        ControlType.DATE,
        ControlType.TEXT,
        ControlType.SELECT,
    },
}


class TemplateLocked(ValidationError):
    """Raised when a published or approved TemplateVersion is edited in place."""


def _values_match(field, new_value, stored_value):
    """Compare a pending value with the stored one, ignoring type-only changes.

    An unsaved instance may hold a ``float`` where the database returns a
    ``Decimal``; treating that as a change would wrongly trip the lock.
    """
    if new_value is None or stored_value is None:
        return new_value is stored_value
    if isinstance(field, models.DecimalField):
        return decimal.Decimal(str(new_value)) == decimal.Decimal(str(stored_value))
    return new_value == stored_value


class PlanningTemplate(TimeStampedModel):
    """A plan type whose layout is under version control for one school."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="planning_templates"
    )
    plan_type = models.CharField(max_length=20, choices=PlanType.choices)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("school", "plan_type")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "plan_type"), name="unique_school_planning_template"
            )
        ]

    @property
    def current_version(self):
        """The version currently published for production use."""
        return self.versions.filter(status=TemplateVersion.Status.CURRENT).first()

    def __str__(self):
        return f"{self.name} ({self.get_plan_type_display()})"


class TemplateVersion(TimeStampedModel):
    """Immutable definition: sources, checksums, coordinates and approval record.

    Lifecycle follows the locking procedure in 8.7:
    ``draft`` → ``in_review`` → ``approved`` → ``current`` → ``superseded``.
    Only a draft may be edited; everything else is frozen.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"
        CURRENT = "current", "Current"
        SUPERSEDED = "superseded", "Superseded"

    #: Statuses whose definition may no longer change.
    LOCKED_STATUSES = frozenset({Status.APPROVED, Status.CURRENT, Status.SUPERSEDED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="template_versions"
    )
    template = models.ForeignKey(
        PlanningTemplate, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # Page geometry. Lesson Plan: A4 portrait 595.32 x 841.92 pt.
    # Work Plan: US Letter landscape 792 x 612 pt across three pages (8.3).
    page_count = models.PositiveSmallIntegerField(default=1)
    page_width_pt = models.DecimalField(max_digits=8, decimal_places=2, default=595.32)
    page_height_pt = models.DecimalField(max_digits=8, decimal_places=2, default=841.92)

    # Source assets and integrity (8.7: "Store both uploaded PDFs, checksums...").
    annotation_source_name = models.CharField(
        max_length=255, blank=True, help_text="Annotated specification file, e.g. TEMPLATE.pdf."
    )
    annotation_source_sha256 = models.CharField(max_length=64, blank=True)
    clean_master_name = models.CharField(
        max_length=255, blank=True, help_text="Unmarked production master used for rendering."
    )
    clean_master_sha256 = models.CharField(max_length=64, blank=True)
    clean_master_approved = models.BooleanField(
        default=False,
        help_text="Set once the school supplies or approves an unmarked production master.",
    )

    notes = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_template_versions",
    )
    published_at = models.DateTimeField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("template", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("template", "version"), name="unique_template_version_number"
            ),
            models.UniqueConstraint(
                fields=("template",),
                condition=Q(status="current"),
                name="one_current_version_per_template",
            ),
        ]
        indexes = [models.Index(fields=("school", "status"))]

    @staticmethod
    def checksum(data: bytes) -> str:
        """SHA-256 of a stored source asset."""
        return hashlib.sha256(data).hexdigest()

    @property
    def is_locked(self):
        return self.status in self.LOCKED_STATUSES

    @property
    def is_renderable(self):
        """A version may only produce production PDFs from an approved clean master.

        Section 2 production-master constraint: TEMPLATE.pdf is a flattened raster
        whose annotation circles are part of the image, so it can never back final
        output.
        """
        return self.clean_master_approved and bool(self.clean_master_sha256)

    def clean(self):
        super().clean()
        if self.template_id and self.template.school_id != self.school_id:
            raise ValidationError({"template": "The template belongs to a different school."})
        if self.status in {self.Status.APPROVED, self.Status.CURRENT} and not self.approved_at:
            raise ValidationError({"approved_at": "An approved version needs an approval record."})
        if self.status == self.Status.CURRENT and not self.is_renderable:
            raise ValidationError(
                "A version cannot become current until its clean master is approved."
            )

    def save(self, *args, **kwargs):
        if self.pk:
            stored = TemplateVersion.all_objects.filter(pk=self.pk).first()
            if stored and stored.is_locked:
                # Publishing an approved version is the only permitted mutation.
                allowed = {"status", "published_at", "updated_at"}
                changed = {
                    field.name
                    for field in self._meta.concrete_fields
                    if not _values_match(
                        field, getattr(self, field.attname), getattr(stored, field.attname)
                    )
                }
                if not changed <= allowed:
                    raise TemplateLocked(
                        "This template version is locked. Create a new version instead."
                    )
        super().save(*args, **kwargs)

    def field_map(self):
        """Ordered field register, ready for the API metadata endpoint.

        Scoped explicitly to this version's school so the call works in
        background jobs where no request tenant context is active.
        """
        return TemplateField.all_objects.filter(
            school_id=self.school_id, template_version_id=self.pk
        ).order_by("page", "sequence", "field_id")

    def __str__(self):
        return f"{self.template.name} v{self.version} ({self.get_status_display()})"


class TemplateField(TimeStampedModel):
    """One annotated region: stable ID, control, validation and PDF box (8.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="template_fields"
    )
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.CASCADE, related_name="fields"
    )
    field_id = models.CharField(
        max_length=16, help_text="Stable register ID, for example LP-D01 or WP-T02."
    )
    label = models.CharField(max_length=160, help_text="Printed label on the template.")
    kind = models.CharField(max_length=10, choices=FieldKind.choices)
    control = models.CharField(max_length=24, choices=ControlType.choices)
    page = models.PositiveSmallIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=1)

    is_required = models.BooleanField(default=False)
    is_readonly = models.BooleanField(default=False)
    help_text = models.CharField(max_length=255, blank=True)

    # Option sourcing for RED controls.
    option_source = models.CharField(
        max_length=64,
        blank=True,
        help_text="Authorised source key, e.g. 'curriculum.subtopic' or 'roster.boys'.",
    )
    depends_on = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dependents",
        help_text="Parent field in a cascading dependency.",
    )

    # Bounds for text and integer controls.
    min_value = models.IntegerField(null=True, blank=True)
    max_value = models.IntegerField(null=True, blank=True)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    max_selections = models.PositiveSmallIntegerField(null=True, blank=True)

    # Measured placement register (8.2), PDF points, top-left origin.
    box_x1 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    box_y1 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    box_x2 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    box_y2 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    overflow_policy = models.CharField(
        max_length=24,
        blank=True,
        help_text="Approved wrap/clip/continuation behaviour for this box.",
    )
    source_note = models.CharField(max_length=255, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("template_version", "page", "sequence", "field_id")
        constraints = [
            models.UniqueConstraint(
                fields=("template_version", "field_id"), name="unique_version_field_id"
            )
        ]
        indexes = [models.Index(fields=("school", "template_version", "kind"))]

    @property
    def box(self):
        """The measured PDF box as a 4-tuple, or ``None`` when unmeasured."""
        corners = (self.box_x1, self.box_y1, self.box_x2, self.box_y2)
        return (
            tuple(float(value) for value in corners)
            if all(value is not None for value in corners)
            else None
        )

    @property
    def is_controlled(self):
        return self.kind == FieldKind.RED

    @property
    def is_free_text(self):
        return self.kind == FieldKind.BLUE

    def clean(self):
        super().clean()
        if self.template_version_id and self.template_version.school_id != self.school_id:
            raise ValidationError({"template_version": "That version belongs to another school."})

        allowed = ALLOWED_CONTROLS.get(self.kind, set())
        if self.control not in allowed:
            raise ValidationError(
                {
                    "control": (
                        f"A {self.get_kind_display()} field cannot use the "
                        f"{self.get_control_display()} control."
                    )
                }
            )
        # 8.1: RED means no uncontrolled typing, so an option source is mandatory.
        if self.kind == FieldKind.RED and not self.option_source:
            raise ValidationError(
                {"option_source": "A controlled field must declare its authorised option source."}
            )
        if self.kind == FieldKind.BLUE and self.is_readonly:
            raise ValidationError({"is_readonly": "A free-text field cannot be read-only."})
        if self.control == ControlType.INTEGER_PICKER and self.min_value is None:
            raise ValidationError({"min_value": "A bounded picker needs a minimum value."})
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.max_value < self.min_value
        ):
            raise ValidationError({"max_value": "The maximum must be at least the minimum."})
        if self.depends_on_id and self.depends_on.template_version_id != self.template_version_id:
            raise ValidationError({"depends_on": "A dependency must live in the same version."})
        if self.page > self.template_version.page_count:
            raise ValidationError({"page": "The page is outside this template version."})

        box_values = (self.box_x1, self.box_y1, self.box_x2, self.box_y2)
        if any(value is not None for value in box_values):
            if any(value is None for value in box_values):
                raise ValidationError("Provide all four box coordinates or none.")
            if self.box_x2 <= self.box_x1 or self.box_y2 <= self.box_y1:
                raise ValidationError("The box must have a positive width and height.")

    def save(self, *args, **kwargs):
        version = self.template_version
        if version and version.is_locked:
            raise TemplateLocked(
                "Fields cannot change on a locked template version. Create a new version."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.field_id} — {self.label}"


class TemplateFieldOption(TimeStampedModel):
    """Static, template-owned options for a controlled field (11).

    Curriculum-driven options are resolved live through
    ``apps.curriculum.services``; this model covers fixed lists such as the
    Lesson Plan RESOURCES prompts (LP-S05) or approved Work Plan event labels.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="template_field_options"
    )
    field = models.ForeignKey(TemplateField, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=120)
    label = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_until = models.DateField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("field", "sequence", "label")
        constraints = [
            models.UniqueConstraint(fields=("field", "value"), name="unique_field_option_value")
        ]

    def is_selectable(self, on_date=None):
        """Invalidated options stay readable on history but cannot be chosen (8.4)."""
        on_date = on_date or timezone.localdate()
        if not self.is_active:
            return False
        if self.effective_from and self.effective_from > on_date:
            return False
        if self.effective_until and self.effective_until < on_date:
            return False
        return True

    def clean(self):
        super().clean()
        if self.field_id and self.field.school_id != self.school_id:
            raise ValidationError({"field": "The field belongs to a different school."})
        if self.field_id and self.field.kind != FieldKind.RED:
            raise ValidationError({"field": "Only controlled RED fields can carry options."})

    def __str__(self):
        return f"{self.field.field_id} · {self.label}"
