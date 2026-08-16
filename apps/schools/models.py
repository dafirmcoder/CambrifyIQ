import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.tenant import get_current_school_id


class SchoolScopedQuerySet(models.QuerySet):
    def for_school(self, school_or_id):
        school_id = getattr(school_or_id, "pk", school_or_id)
        return self.filter(school_id=school_id)


class SchoolScopedManager(models.Manager.from_queryset(SchoolScopedQuerySet)):
    """A fail-closed manager for tenant-owned rows."""

    def get_queryset(self):
        queryset = super().get_queryset()
        school_id = get_current_school_id()
        return queryset.filter(school_id=school_id) if school_id else queryset.none()

    def for_school(self, school_or_id):
        school_id = getattr(school_or_id, "pk", school_or_id)
        return super().get_queryset().filter(school_id=school_id)


class School(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=190, unique=True)
    code = models.CharField(max_length=24, unique=True)
    timezone = models.CharField(max_length=64, default="Africa/Dar_es_Salaam")
    country = models.CharField(max_length=2, default="TZ")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(blank=True)
    logo_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    onboarding_complete = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_schools",
    )

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        TEACHER = "teacher", "Teacher"
        COORDINATOR = "coordinator", "Curriculum Coordinator"
        HEAD = "head", "Head of Cambridge"
        DIRECTOR = "director", "School Director"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    job_title = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("school__name", "user__full_name")
        constraints = [
            models.UniqueConstraint(fields=("school", "user"), name="unique_school_user_membership")
        ]
        indexes = [models.Index(fields=("school", "role", "status"))]

    @property
    def can_manage_users(self):
        return self.status == self.Status.ACTIVE and self.role in {
            self.Role.HEAD,
            self.Role.DIRECTOR,
        }

    @property
    def can_manage_school(self):
        return self.can_manage_users

    @property
    def role_badge(self):
        return self.get_role_display()

    def __str__(self):
        return f"{self.user.email} · {self.school.name} · {self.get_role_display()}"


class Invitation(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Membership.Role.choices)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_school_invitations"
    )
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    accepted_at = models.DateTimeField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("school", "email", "status"))]

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @property
    def is_usable(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.email} invited to {self.school.name}"


class AcademicYear(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="academic_years")
    name = models.CharField(max_length=32, help_text="For example, 2026/2027")
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current = models.BooleanField(default=False)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-starts_on",)
        constraints = [
            models.UniqueConstraint(fields=("school", "name"), name="unique_school_academic_year"),
            models.CheckConstraint(
                condition=Q(ends_on__gt=models.F("starts_on")), name="year_ends_after_start"
            ),
        ]

    def __str__(self):
        return self.name


class Term(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="terms")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=80)
    sequence = models.PositiveSmallIntegerField(default=1)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("academic_year", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("academic_year", "sequence"), name="unique_term_sequence"
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gt=models.F("starts_on")), name="term_ends_after_start"
            ),
        ]

    def clean(self):
        if self.academic_year_id and self.school_id != self.academic_year.school_id:
            raise ValidationError("The academic year must belong to the same school.")

    def __str__(self):
        return f"{self.academic_year.name} · {self.name}"


class Subject(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="subjects")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32)
    cambridge_code = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("school", "code"), name="unique_school_subject_code")
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"


class SchoolClass(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField(max_length=80)
    year_group = models.CharField(max_length=40, blank=True)
    boys_count = models.PositiveIntegerField(default=0)
    girls_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("school", "name"), name="unique_school_class_name")
        ]
        verbose_name_plural = "school classes"

    @property
    def roster_total(self):
        return self.boys_count + self.girls_count

    def __str__(self):
        return self.name


class TeacherAssignment(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="teacher_assignments")
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="teacher_assignments"
    )
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.PROTECT, related_name="teacher_assignments"
    )
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("subject__name", "school_class__name")
        indexes = [models.Index(fields=("school", "teacher", "is_active"))]
        constraints = [
            models.UniqueConstraint(
                fields=("school", "teacher", "subject", "school_class", "effective_from"),
                name="unique_teacher_assignment_period",
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True)
                | Q(effective_until__gte=models.F("effective_from")),
                name="assignment_valid_date_range",
            ),
        ]

    def clean(self):
        related_school_ids = {self.subject.school_id, self.school_class.school_id}
        if related_school_ids != {self.school_id}:
            raise ValidationError("Subject and class must belong to the assignment school.")
        if not Membership.objects.filter(
            school_id=self.school_id,
            user_id=self.teacher_id,
            role=Membership.Role.TEACHER,
            status=Membership.Status.ACTIVE,
        ).exists():
            raise ValidationError(
                {"teacher": "The teacher needs an active teacher membership in this school."}
            )

    def __str__(self):
        return f"{self.teacher.get_short_name()} · {self.subject.code} · {self.school_class.name}"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        School, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("school", "action", "created_at"))]

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records are immutable.")

    def __str__(self):
        return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M}"
