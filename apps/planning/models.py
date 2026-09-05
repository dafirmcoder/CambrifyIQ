"""School-owned planning template definitions and their immutable publications."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.curriculum.models import LearningObjective, SchemeOfWork, Subtopic, Topic
from apps.schools.models import (
    AcademicYear,
    CalendarWeek,
    School,
    SchoolScopedManager,
    TeacherAssignment,
    Term,
)


class PlanningTemplate(TimeStampedModel):
    class TemplateType(models.TextChoices):
        SEMESTER_WORK_PLAN = "semester_work_plan", "Semester Work Plan"
        LESSON_PLAN = "lesson_plan", "Lesson Plan"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="planning_templates")
    template_type = models.CharField(max_length=32, choices=TemplateType.choices)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("template_type", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "template_type", "name"), name="unique_school_planning_template"
            )
        ]

    def __str__(self):
        return self.name


class TemplateVersion(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="template_versions")
    template = models.ForeignKey(
        PlanningTemplate, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    source_asset = models.FileField(upload_to="planning-template-masters/", blank=True)
    source_filename = models.CharField(max_length=255, blank=True)
    source_checksum = models.CharField(max_length=64, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("template", "-version")
        constraints = [
            models.UniqueConstraint(fields=("template", "version"), name="unique_template_version"),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True)
                | Q(effective_from__isnull=True)
                | Q(effective_until__gte=models.F("effective_from")),
                name="template_version_valid_effective_dates",
            ),
        ]

    @property
    def is_locked(self):
        return self.status in {self.Status.PUBLISHED, self.Status.RETIRED}

    def clean(self):
        if self.template_id:
            try:
                template = getattr(self, "template", None) or PlanningTemplate.all_objects.get(
                    pk=self.template_id
                )
            except PlanningTemplate.DoesNotExist:
                template = None
            if template and self.school_id != template.school_id:
                raise ValidationError({"template": "The template must belong to the same school."})
        if self.status == self.Status.PUBLISHED and not self.effective_from:
            raise ValidationError({"effective_from": "Published versions need an effective date."})
        if self.source_checksum and len(self.source_checksum) != 64:
            raise ValidationError({"source_checksum": "Use a SHA-256 hexadecimal checksum."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            current = TemplateVersion.all_objects.get(pk=self.pk)
            if current.is_locked:
                raise ValidationError("Published and retired template versions are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_locked:
            raise ValidationError("Published and retired template versions cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.template} v{self.version}"


class TemplateField(TimeStampedModel):
    class FieldClass(models.TextChoices):
        RED = "red", "Controlled"
        BLUE = "blue", "Teacher-entered"
        SYSTEM = "system", "System-generated"

    class ControlType(models.TextChoices):
        TEXT = "text", "Single-line text"
        TEXTAREA = "textarea", "Multi-line text"
        SELECT = "select", "Static picker"
        DATE = "date", "Date"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Yes/no"
        SYSTEM = "system", "System-generated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="template_fields")
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.CASCADE, related_name="fields"
    )
    field_id = models.CharField(max_length=48)
    label = models.CharField(max_length=160)
    field_class = models.CharField(max_length=12, choices=FieldClass.choices)
    control_type = models.CharField(max_length=16, choices=ControlType.choices)
    is_required = models.BooleanField(default=False)
    sequence = models.PositiveSmallIntegerField()
    page_number = models.PositiveSmallIntegerField(default=1)
    x = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    y = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    width = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    height = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    help_text = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("template_version", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("template_version", "field_id"), name="unique_version_field_id"
            ),
            models.UniqueConstraint(
                fields=("template_version", "sequence"), name="unique_version_field_sequence"
            ),
            models.CheckConstraint(
                condition=Q(width__isnull=True) | Q(width__gt=0), name="field_width_positive"
            ),
            models.CheckConstraint(
                condition=Q(height__isnull=True) | Q(height__gt=0), name="field_height_positive"
            ),
        ]

    def clean(self):
        if self.template_version_id:
            try:
                version = getattr(
                    self, "template_version", None
                ) or TemplateVersion.all_objects.get(pk=self.template_version_id)
            except TemplateVersion.DoesNotExist:
                version = None
            if version:
                if self.school_id != version.school_id:
                    raise ValidationError(
                        {"template_version": "The version must belong to the same school."}
                    )
                if version.is_locked:
                    raise ValidationError("Fields of a published or retired version are immutable.")
        coordinates = (self.x, self.y, self.width, self.height)
        if any(value is not None for value in coordinates) and any(
            value is None for value in coordinates
        ):
            raise ValidationError("PDF field placement requires x, y, width and height.")

    def save(self, *args, **kwargs):
        if (
            not self._state.adding
            and TemplateField.all_objects.get(pk=self.pk).template_version.is_locked
        ):
            raise ValidationError("Fields of a published or retired version are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.template_version.is_locked:
            raise ValidationError("Fields of a published or retired version cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.field_id}: {self.label}"


class TemplateFieldOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="template_field_options"
    )
    field = models.ForeignKey(TemplateField, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=120)
    label = models.CharField(max_length=160)
    sequence = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("field", "sequence")
        constraints = [
            models.UniqueConstraint(fields=("field", "value"), name="unique_field_option_value"),
            models.UniqueConstraint(
                fields=("field", "sequence"), name="unique_field_option_sequence"
            ),
        ]

    def clean(self):
        if self.field_id:
            try:
                field = getattr(self, "field", None) or TemplateField.all_objects.get(
                    pk=self.field_id
                )
            except TemplateField.DoesNotExist:
                field = None
            if field:
                if self.school_id != field.school_id:
                    raise ValidationError({"field": "The field must belong to the same school."})
                if field.template_version.is_locked:
                    raise ValidationError(
                        "Options of a published or retired version are immutable."
                    )

    def save(self, *args, **kwargs):
        if (
            not self._state.adding
            and TemplateFieldOption.all_objects.get(pk=self.pk).field.template_version.is_locked
        ):
            raise ValidationError("Options of a published or retired version are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.field.template_version.is_locked:
            raise ValidationError("Options of a published or retired version cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.label


class WorkPlan(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        RETURNED = "returned", "Returned"
        RESUBMITTED = "resubmitted", "Resubmitted"
        APPROVED = "approved", "Approved"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="work_plans")
    subject = models.ForeignKey(
        "schools.Subject", null=True, blank=True, on_delete=models.PROTECT, related_name="work_plans"
    )
    school_class = models.ForeignKey(
        "schools.SchoolClass", null=True, blank=True, on_delete=models.PROTECT, related_name="work_plans"
    )
    assignment = models.ForeignKey(
        TeacherAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name="work_plans"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="work_plans"
    )
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="work_plans")
    scheme = models.ForeignKey(SchemeOfWork, on_delete=models.PROTECT, related_name="work_plans")
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name="work_plans"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_plans"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    revision_token = models.UUIDField(default=uuid.uuid4, editable=False)
    resources = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("school", "term", "subject", "school_class"),
                condition=~Q(status="archived"),
                name="one_active_work_plan_per_subject_class_term",
            )
        ]
        indexes = [models.Index(fields=("school", "status", "updated_at"))]

    @property
    def is_editable(self):
        return self.status in {self.Status.DRAFT, self.Status.RETURNED}

    @property
    def subject_display(self):
        if self.subject:
            return self.subject.name
        if self.assignment and self.assignment.subject:
            return self.assignment.subject.name
        return self.scheme.subject_name if self.scheme else ""

    @property
    def class_display(self):
        if self.school_class:
            return self.school_class.name
        if self.assignment and self.assignment.school_class:
            return self.assignment.school_class.name
        return ""

    @property
    def year_group(self):
        if self.school_class and self.school_class.year_group:
            return self.school_class.year_group
        if self.assignment and self.assignment.school_class and self.assignment.school_class.year_group:
            return self.assignment.school_class.year_group
        return self.scheme.year_group if self.scheme else ""

    def clean(self):
        if self.assignment:
            if not self.subject_id:
                self.subject = self.assignment.subject
            if not self.school_class_id:
                self.school_class = self.assignment.school_class

        if not self.subject_id or not self.school_class_id:
            raise ValidationError("A Work Plan requires a valid Subject and School Class.")

        school_ids = {
            self.subject.school_id,
            self.school_class.school_id,
            self.academic_year.school_id,
            self.term.school_id,
            self.template_version.school_id,
        }
        if self.assignment:
            school_ids.add(self.assignment.school_id)

        if school_ids != {self.school_id}:
            raise ValidationError(
                "Subject, class, calendar and template must belong to the plan school."
            )
        if self.term.academic_year_id != self.academic_year_id:
            raise ValidationError({"term": "The term must belong to the selected academic year."})
        if (
            self.template_version.template.template_type
            != PlanningTemplate.TemplateType.SEMESTER_WORK_PLAN
        ):
            raise ValidationError(
                {"template_version": "A Work Plan needs a Semester Work Plan template."}
            )

    def __str__(self):
        return f"{self.subject_display} · {self.class_display} · {self.term}"


class WorkPlanWeek(TimeStampedModel):
    """A historical snapshot of a school CalendarWeek within a Work Plan."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="work_plan_weeks")
    work_plan = models.ForeignKey(WorkPlan, on_delete=models.CASCADE, related_name="weeks")
    calendar_week = models.ForeignKey(
        CalendarWeek, on_delete=models.PROTECT, related_name="work_plan_weeks"
    )
    sequence = models.PositiveSmallIntegerField()
    month_label = models.CharField(max_length=32, blank=True)
    week_label = models.CharField(max_length=100)
    event_label = models.CharField(max_length=160, blank=True)
    is_instructional = models.BooleanField(default=True)
    topic = models.ForeignKey(
        Topic, null=True, blank=True, on_delete=models.PROTECT, related_name="work_plan_weeks"
    )
    subtopic = models.ForeignKey(
        Subtopic, null=True, blank=True, on_delete=models.PROTECT, related_name="work_plan_weeks"
    )
    lessons_per_week = models.PositiveSmallIntegerField(default=1)
    remarks = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("work_plan", "sequence")
        constraints = [
            models.UniqueConstraint(fields=("work_plan", "sequence"), name="unique_work_plan_week"),
            models.UniqueConstraint(
                fields=("work_plan", "calendar_week"), name="unique_work_plan_calendar_week"
            ),
        ]

    def clean(self):
        if (
            self.school_id != self.work_plan.school_id
            or self.school_id != self.calendar_week.school_id
        ):
            raise ValidationError("The plan week, calendar week and school must match.")
        if self.calendar_week.term_id != self.work_plan.term_id:
            raise ValidationError(
                {"calendar_week": "The calendar week must belong to the plan term."}
            )
        if self.topic_id and self.topic.scheme_id != self.work_plan.scheme_id:
            raise ValidationError({"topic": "The topic must belong to the plan scheme."})
        if self.subtopic_id:
            if not self.topic_id:
                raise ValidationError({"subtopic": "A Unit requires a primary topic."})
            if self.subtopic.topic_id != self.topic_id:
                raise ValidationError({"subtopic": "The Unit must belong to the selected topic."})
            if self.subtopic.topic.scheme_id != self.work_plan.scheme_id:
                raise ValidationError({"subtopic": "The Unit must belong to the plan scheme."})
        if not self.is_instructional:
            if self.topic_id:
                raise ValidationError(
                    {"topic": "Special-event weeks cannot have a curriculum topic."}
                )
            if self.subtopic_id:
                raise ValidationError(
                    {"subtopic": "Special-event weeks cannot have a curriculum unit."}
                )
            if self.lessons_per_week != 0:
                raise ValidationError(
                    {"lessons_per_week": "Special-event weeks must have 0 lessons."}
                )
        else:
            if self.lessons_per_week < 1:
                raise ValidationError(
                    {"lessons_per_week": "Instructional weeks must have at least 1 lesson."}
                )

    def __str__(self):
        return self.week_label


class WorkPlanWeekObjective(TimeStampedModel):
    """Selected LOs with a label snapshot for reproducible approved plans."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="work_plan_objectives"
    )
    work_plan_week = models.ForeignKey(
        WorkPlanWeek, on_delete=models.CASCADE, related_name="objective_selections"
    )
    objective = models.ForeignKey(
        LearningObjective, on_delete=models.PROTECT, related_name="work_plan_selections"
    )
    code_snapshot = models.CharField(max_length=64)
    text_snapshot = models.TextField()
    is_met = models.BooleanField(default=False)
    met_at = models.DateTimeField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("work_plan_week", "code_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("work_plan_week", "objective"), name="unique_week_learning_objective"
            )
        ]

    def clean(self):
        week = self.work_plan_week
        if self.school_id != week.school_id:
            raise ValidationError("The objective selection must belong to the plan school.")
        from apps.curriculum.cross_curriculum import is_cross_subject_borrowable

        if self.objective.scheme_id != week.work_plan.scheme_id and not is_cross_subject_borrowable(
            week.work_plan.scheme, self.objective.scheme
        ):
            raise ValidationError(
                {"objective": "The objective must belong to the plan scheme or an eligible cross-curricular scheme."}
            )
        if not week.is_instructional:
            raise ValidationError("Special-event weeks cannot have learning objectives.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.code_snapshot = self.objective.code
            self.text_snapshot = self.objective.text
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code_snapshot


class WorkPlanEvent(models.Model):
    """Immutable audit trail of each Work Plan workflow transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="work_plan_events")
    work_plan = models.ForeignKey(WorkPlan, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=20, choices=WorkPlan.Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=WorkPlan.Status.choices)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("school", "work_plan", "created_at"))]

    def clean(self):
        if self.school_id != self.work_plan.school_id:
            raise ValidationError("The workflow event must belong to the plan school.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Work Plan workflow events are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Work Plan workflow events are immutable.")


class LessonPlan(TimeStampedModel):
    """An assignment-scoped lesson using controlled curriculum and attendance data."""

    Status = WorkPlan.Status

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="lesson_plans")
    assignment = models.ForeignKey(
        TeacherAssignment, on_delete=models.PROTECT, related_name="lesson_plans"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="lesson_plans"
    )
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="lesson_plans")
    scheme = models.ForeignKey(SchemeOfWork, on_delete=models.PROTECT, related_name="lesson_plans")
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name="lesson_plans"
    )
    originating_work_plan_week = models.ForeignKey(
        WorkPlanWeek,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="lesson_plans",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lesson_plans"
    )
    lesson_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    schedule_slot = models.ForeignKey(
        "schools.TeacherScheduleSlot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lesson_plans",
    )
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="lesson_plans")
    subtopic = models.ForeignKey(
        Subtopic, null=True, blank=True, on_delete=models.PROTECT, related_name="lesson_plans"
    )
    boys_attendance = models.PositiveSmallIntegerField(null=True, blank=True)
    girls_attendance = models.PositiveSmallIntegerField(null=True, blank=True)
    main_teaching_activity = models.TextField()
    assessment_ideas = models.TextField()
    resources = models.JSONField(default=list, blank=True)
    notes_remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    revision_token = models.UUIDField(default=uuid.uuid4, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-lesson_date", "-updated_at")
        indexes = [models.Index(fields=("school", "status", "lesson_date"))]

    @property
    def total_attendance(self):
        if self.boys_attendance is None and self.girls_attendance is None:
            return None
        return (self.boys_attendance or 0) + (self.girls_attendance or 0)

    @property
    def lesson_end_datetime(self):
        from datetime import time, datetime
        et = self.end_time or time(23, 59, 59)
        dt = datetime.combine(self.lesson_date, et)
        tz = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, tz)
        return dt

    @property
    def is_lesson_past(self):
        return self.lesson_end_datetime <= timezone.localtime()

    @property
    def is_editable(self):
        return self.status in {self.Status.DRAFT, self.Status.RETURNED}

    def clean(self):
        if isinstance(self.lesson_date, str):
            from django.utils.dateparse import parse_date
            parsed = parse_date(self.lesson_date)
            if parsed:
                self.lesson_date = parsed

        school_ids = {
            self.assignment.school_id,
            self.academic_year.school_id,
            self.term.school_id,
            self.template_version.school_id,
        }
        if school_ids != {self.school_id}:
            raise ValidationError(
                "Assignment, calendar and template must belong to the Lesson Plan school."
            )
        if self.term.academic_year_id != self.academic_year_id:
            raise ValidationError({"term": "The term must belong to the selected academic year."})
        if self.lesson_date and not (self.term.starts_on <= self.lesson_date <= self.term.ends_on):
            raise ValidationError(
                {"lesson_date": "The lesson date must fall within the selected term."}
            )
        if (
            self.template_version.template.template_type
            != PlanningTemplate.TemplateType.LESSON_PLAN
        ):
            raise ValidationError(
                {"template_version": "A Lesson Plan needs a Lesson Plan template."}
            )
        if self.author_id != self.assignment.teacher_id:
            raise ValidationError(
                {"author": "The assignment teacher must author this Lesson Plan."}
            )
        if self.topic.scheme_id != self.scheme_id:
            raise ValidationError({"topic": "The topic must belong to the selected scheme."})
        if self.subtopic_id and self.subtopic.topic_id != self.topic_id:
            raise ValidationError({"subtopic": "The subtopic must belong to the selected topic."})
        if self.boys_attendance is not None and self.boys_attendance > self.assignment.school_class.boys_count:
            raise ValidationError(
                {"boys_attendance": "Attendance cannot exceed the class boys roster."}
            )
        if self.girls_attendance is not None and self.girls_attendance > self.assignment.school_class.girls_count:
            raise ValidationError(
                {"girls_attendance": "Attendance cannot exceed the class girls roster."}
            )

        # Prospective planning constraint for new lesson plans
        if self._state.adding and self.lesson_date:
            if self.is_lesson_past:
                raise ValidationError(
                    {"lesson_date": "Lesson plans cannot be created for past lessons or elapsed lesson times. Planning must be prospective."}
                )
        if self.originating_work_plan_week_id:
            origin = self.originating_work_plan_week
            wp = origin.work_plan
            if wp.subject_id and wp.subject_id != self.assignment.subject_id:
                raise ValidationError(
                    {"originating_work_plan_week": "The Work Plan row must match the assignment subject."}
                )
            if wp.school_class_id and wp.school_class_id != self.assignment.school_class_id:
                raise ValidationError(
                    {"originating_work_plan_week": "The Work Plan row must match the assignment class."}
                )
            if origin.topic_id and origin.topic_id != self.topic_id:
                raise ValidationError(
                    {"topic": "The topic must match the originating Work Plan row."}
                )

    def __str__(self):
        return f"{self.assignment} · {self.lesson_date:%d %b %Y}"


class LessonPlanObjective(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="lesson_plan_objectives"
    )
    lesson_plan = models.ForeignKey(
        LessonPlan, on_delete=models.CASCADE, related_name="objective_selections"
    )
    objective = models.ForeignKey(
        LearningObjective, on_delete=models.PROTECT, related_name="lesson_plan_selections"
    )
    code_snapshot = models.CharField(max_length=64, blank=True)
    text_snapshot = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("lesson_plan", "code_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("lesson_plan", "objective"), name="unique_lesson_plan_objective"
            )
        ]

    def clean(self):
        if self.objective_id and not self.code_snapshot:
            self.code_snapshot = self.objective.code
            self.text_snapshot = self.objective.text
        if self.school_id != self.lesson_plan.school_id:
            raise ValidationError("The objective must belong to the Lesson Plan school.")
        from apps.curriculum.cross_curriculum import is_cross_subject_borrowable

        if self.objective.scheme_id != self.lesson_plan.scheme_id and not is_cross_subject_borrowable(
            self.lesson_plan.scheme, self.objective.scheme
        ):
            raise ValidationError(
                {"objective": "The objective must belong to the selected scheme or an eligible cross-curricular scheme."}
            )

    def save(self, *args, **kwargs):
        if self.objective_id and not self.code_snapshot:
            self.code_snapshot = self.objective.code
            self.text_snapshot = self.objective.text
        self.full_clean()
        super().save(*args, **kwargs)


class LessonPlanEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="lesson_plan_events")
    lesson_plan = models.ForeignKey(LessonPlan, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=20, choices=LessonPlan.Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=LessonPlan.Status.choices)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Lesson Plan workflow events are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Lesson Plan workflow events are immutable.")
