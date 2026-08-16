"""Cambridge curriculum repository.

Implements the Phase 1 "Curriculum & Access Core" entities from the CAMS plan
(sections 7.1 and 11): versioned schemes of work, their topic/subtopic hierarchy
and the learning/assessment objectives that drive the controlled pickers in the
Work Plan (WP-D08) and Lesson Plan (LP-D01, LP-D04) builders.

Governance rules enforced here:

* Every row is school-scoped and fails closed through ``SchoolScopedManager``.
* Objectives carry stable codes so approved plans stay reproducible.
* Deactivated rows remain readable on historical plans but are excluded from the
  ``selectable()`` querysets that back option endpoints.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.schools.models import (
    AcademicYear,
    SchoolClass,
    SchoolScopedManager,
    SchoolScopedQuerySet,
    Subject,
    Term,
)


class CurriculumQuerySet(SchoolScopedQuerySet):
    """Shared filters for every curriculum row."""

    def active(self):
        return self.filter(is_active=True)

    def effective_on(self, on_date=None):
        on_date = on_date or timezone.localdate()
        return self.filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=on_date),
            Q(effective_until__isnull=True) | Q(effective_until__gte=on_date),
        )

    def selectable(self, on_date=None):
        """Rows a builder may offer as a new choice."""
        return self.active().effective_on(on_date)


class CurriculumManager(SchoolScopedManager.from_queryset(CurriculumQuerySet)):
    pass


class EffectiveDatedModel(TimeStampedModel):
    """Common active-window behaviour for curriculum rows."""

    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_until = models.DateField(null=True, blank=True)

    objects = CurriculumManager()
    all_objects = models.Manager.from_queryset(CurriculumQuerySet)()

    class Meta:
        abstract = True

    def is_selectable(self, on_date=None):
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
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until < self.effective_from
        ):
            raise ValidationError(
                {"effective_until": "The end date must fall on or after the start date."}
            )


class SchemeOfWork(EffectiveDatedModel):
    """Authorised sequence of topics and objectives for a subject, level and term."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="schemes_of_work"
    )
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="schemes_of_work")
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name="schemes_of_work",
        help_text="The level or year group this scheme sequences.",
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="schemes_of_work"
    )
    term = models.ForeignKey(
        Term,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="schemes_of_work",
        help_text="Leave empty for a whole-year scheme.",
    )
    title = models.CharField(max_length=180)
    code = models.CharField(max_length=48, help_text="Stable reference, for example SCI-Y8-S1.")
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_schemes",
    )

    class Meta:
        ordering = ("subject__name", "school_class__name", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "code", "version"), name="unique_school_scheme_code_version"
            )
        ]
        indexes = [
            models.Index(fields=("school", "subject", "school_class", "status")),
            models.Index(fields=("school", "status", "is_active")),
        ]
        verbose_name = "scheme of work"
        verbose_name_plural = "schemes of work"

    def clean(self):
        super().clean()
        related = {
            "subject": self.subject_id and self.subject.school_id,
            "school_class": self.school_class_id and self.school_class.school_id,
            "academic_year": self.academic_year_id and self.academic_year.school_id,
        }
        if self.term_id:
            related["term"] = self.term.school_id
            if self.term.academic_year_id != self.academic_year_id:
                raise ValidationError(
                    {"term": "The term must belong to the selected academic year."}
                )
        for field, school_id in related.items():
            if school_id and school_id != self.school_id:
                raise ValidationError({field: "This record belongs to a different school."})

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    def __str__(self):
        return f"{self.code} v{self.version} — {self.title}"


class Topic(EffectiveDatedModel):
    """A unit within a scheme. Backs the first level of Lesson Plan LP-D01."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="topics")
    scheme = models.ForeignKey(SchemeOfWork, on_delete=models.CASCADE, related_name="topics")
    code = models.CharField(max_length=48)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    suggested_weeks = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ("scheme", "sequence", "code")
        constraints = [
            models.UniqueConstraint(fields=("scheme", "code"), name="unique_scheme_topic_code")
        ]
        indexes = [models.Index(fields=("school", "scheme", "sequence"))]

    def clean(self):
        super().clean()
        if self.scheme_id and self.scheme.school_id != self.school_id:
            raise ValidationError({"scheme": "The scheme belongs to a different school."})

    def __str__(self):
        return f"{self.code} — {self.title}"


class Subtopic(EffectiveDatedModel):
    """A sub-unit within a topic. Backs the second level of Lesson Plan LP-D01."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="subtopics")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="subtopics")
    code = models.CharField(max_length=48)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ("topic", "sequence", "code")
        constraints = [
            models.UniqueConstraint(fields=("topic", "code"), name="unique_topic_subtopic_code")
        ]
        indexes = [models.Index(fields=("school", "topic", "sequence"))]

    def clean(self):
        super().clean()
        if self.topic_id and self.topic.school_id != self.school_id:
            raise ValidationError({"topic": "The topic belongs to a different school."})

    @property
    def scheme_id(self):
        return self.topic.scheme_id

    def __str__(self):
        return f"{self.code} — {self.title}"


class LearningObjective(EffectiveDatedModel):
    """What learners should know, understand or do. Backs LP-D04 and WP-D08."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="learning_objectives"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="learning_objectives")
    subtopic = models.ForeignKey(
        Subtopic,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="learning_objectives",
    )
    code = models.CharField(max_length=48, help_text="Stable Cambridge or school code.")
    text = models.TextField()
    sequence = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("topic", "sequence", "code")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "topic", "code"), name="unique_topic_learning_objective_code"
            )
        ]
        indexes = [models.Index(fields=("school", "topic", "subtopic", "is_active"))]

    def clean(self):
        super().clean()
        if self.topic_id and self.topic.school_id != self.school_id:
            raise ValidationError({"topic": "The topic belongs to a different school."})
        if self.subtopic_id and self.subtopic.topic_id != self.topic_id:
            raise ValidationError({"subtopic": "The sub-topic must belong to the selected topic."})

    @property
    def label(self):
        """Human-readable label stored on plans so history stays reproducible."""
        return f"{self.code}: {self.text}"

    def __str__(self):
        return self.code


class AssessmentObjective(EffectiveDatedModel):
    """Capability measured under Cambridge assessment criteria."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="assessment_objectives"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="assessment_objectives"
    )
    code = models.CharField(max_length=48)
    text = models.TextField()
    sequence = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    learning_objectives = models.ManyToManyField(
        LearningObjective,
        blank=True,
        related_name="assessment_objectives",
    )

    class Meta:
        ordering = ("subject", "sequence", "code")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "subject", "code"), name="unique_subject_assessment_objective"
            )
        ]
        indexes = [models.Index(fields=("school", "subject", "is_active"))]

    def clean(self):
        super().clean()
        if self.subject_id and self.subject.school_id != self.school_id:
            raise ValidationError({"subject": "The subject belongs to a different school."})

    @property
    def label(self):
        return f"{self.code}: {self.text}"

    def __str__(self):
        return self.code
