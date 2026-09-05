"""Globally administered Cambridge curriculum content.

Curriculum records are intentionally not tenant-owned: system administrators update a
single canonical framework which is available to every school. A school's Subject
``cambridge_code`` and SchoolClass ``year_group`` are the stable mapping values used
by future planning workflows.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class CurriculumFramework(TimeStampedModel):
    """A published curriculum family, such as Cambridge Lower Secondary."""

    class FrameworkCode(models.TextChoices):
        PRIMARY = "CAMBRIDGE_PRIMARY", "Cambridge Primary"
        LOWER_SECONDARY = "CAMBRIDGE_LOWER_SECONDARY", "Cambridge Lower Secondary"
        IGCSE = "CAMBRIDGE_IGCSE", "Cambridge IGCSE"
        AS_A_LEVEL = "CAMBRIDGE_AS_A_LEVEL", "Cambridge International AS & A Level"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    publisher = models.CharField(max_length=120, default="Cambridge International")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.code} — {self.name}"


class SchemeOfWork(TimeStampedModel):
    """A versioned, globally shared scheme for one subject and year group."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    framework = models.ForeignKey(
        CurriculumFramework, on_delete=models.PROTECT, related_name="schemes"
    )
    subject_code = models.CharField(max_length=32)
    subject_name = models.CharField(max_length=120)
    year_group = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    syllabus_years = models.CharField(
        max_length=64, default="2023-2027", blank=True, help_text="e.g. 2023-2027"
    )
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    published_on = models.DateField(null=True, blank=True)
    retired_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ("framework__name", "subject_name", "year_group", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("framework", "subject_code", "year_group", "version"),
                name="unique_global_scheme_version",
            ),
            models.CheckConstraint(
                condition=Q(retired_on__isnull=True)
                | Q(published_on__isnull=True)
                | Q(retired_on__gte=models.F("published_on")),
                name="scheme_retired_after_publication",
            ),
        ]
        indexes = [models.Index(fields=("framework", "subject_code", "year_group", "is_active"))]

    def clean(self):
        if self.retired_on and not self.published_on:
            raise ValidationError({"retired_on": "A retired scheme must have a publication date."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        years_str = f" {self.syllabus_years}" if self.syllabus_years else ""
        return f"{self.subject_code} {self.subject_name} {self.year_group}{years_str}"

    def __str__(self):
        years_str = f" {self.syllabus_years}" if self.syllabus_years else ""
        return f"{self.subject_code} {self.subject_name} ({self.year_group}){years_str}"


class Topic(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.ForeignKey(SchemeOfWork, on_delete=models.CASCADE, related_name="topics")
    code = models.CharField(max_length=48, blank=True)
    title = models.CharField(max_length=200)
    sequence = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("scheme", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("scheme", "sequence"), name="unique_scheme_topic_sequence"
            ),
            models.UniqueConstraint(
                fields=("scheme", "code"),
                condition=~Q(code=""),
                name="unique_nonblank_scheme_topic_code",
            ),
        ]

    def __str__(self):
        return self.title


class Subtopic(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="subtopics")
    code = models.CharField(max_length=48, blank=True)
    title = models.CharField(max_length=200)
    sequence = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("topic", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("topic", "sequence"), name="unique_topic_subtopic_sequence"
            ),
            models.UniqueConstraint(
                fields=("topic", "code"),
                condition=~Q(code=""),
                name="unique_nonblank_topic_subtopic_code",
            ),
        ]

    def __str__(self):
        return self.title


class LearningObjective(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.ForeignKey(
        SchemeOfWork, on_delete=models.CASCADE, related_name="learning_objectives"
    )
    topic = models.ForeignKey(
        Topic, null=True, blank=True, on_delete=models.SET_NULL, related_name="learning_objectives"
    )
    subtopic = models.ForeignKey(
        Subtopic,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="learning_objectives",
    )
    code = models.CharField(max_length=64)
    text = models.TextField()
    sequence = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ("scheme", "sequence", "code")
        constraints = [
            models.UniqueConstraint(fields=("scheme", "code"), name="unique_scheme_lo_code")
        ]

    def clean(self):
        if self.topic_id and self.topic.scheme_id != self.scheme_id:
            raise ValidationError({"topic": "The topic must belong to this scheme."})
        if self.subtopic_id:
            if self.subtopic.topic.scheme_id != self.scheme_id:
                raise ValidationError({"subtopic": "The subtopic must belong to this scheme."})
            if self.topic_id and self.subtopic.topic_id != self.topic_id:
                raise ValidationError(
                    {"subtopic": "The subtopic must belong to the selected topic."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code}: {self.text[:60]}"


class AssessmentObjective(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.ForeignKey(
        SchemeOfWork, on_delete=models.CASCADE, related_name="assessment_objectives"
    )
    code = models.CharField(max_length=64)
    text = models.TextField()
    sequence = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ("scheme", "sequence", "code")
        constraints = [
            models.UniqueConstraint(fields=("scheme", "code"), name="unique_scheme_ao_code")
        ]

    def __str__(self):
        return f"{self.code}: {self.text[:60]}"
