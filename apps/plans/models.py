"""Work Plans, Lesson Plans and their workflow records (plan sections 7.2–7.5, 11).

Both plan types share one workflow state machine (7.5) and one revision-token
autosave contract (8.5). Values are stored against the stable template field IDs
declared in ``apps.planning.register`` so a plan can always be re-rendered
against the exact ``TemplateVersion`` it was authored under.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.schools.models import SchoolScopedManager


class PlanState(models.TextChoices):
    """Workflow states from plan section 7.5."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    RETURNED = "returned", "Returned"
    RESUBMITTED = "resubmitted", "Resubmitted"
    APPROVED = "approved", "Approved"
    ARCHIVED = "archived", "Archived"


#: States in which the author may still edit the plan.
EDITABLE_STATES = frozenset({PlanState.DRAFT, PlanState.RETURNED})
#: States awaiting a reviewer decision.
PENDING_STATES = frozenset({PlanState.SUBMITTED, PlanState.UNDER_REVIEW, PlanState.RESUBMITTED})
#: Permitted transitions. Approved plans are terminal except for archiving.
ALLOWED_TRANSITIONS = {
    PlanState.DRAFT: {PlanState.SUBMITTED},
    PlanState.SUBMITTED: {PlanState.UNDER_REVIEW, PlanState.RETURNED, PlanState.APPROVED},
    PlanState.UNDER_REVIEW: {PlanState.RETURNED, PlanState.APPROVED},
    PlanState.RETURNED: {PlanState.RESUBMITTED},
    PlanState.RESUBMITTED: {PlanState.UNDER_REVIEW, PlanState.RETURNED, PlanState.APPROVED},
    PlanState.APPROVED: {PlanState.ARCHIVED},
    PlanState.ARCHIVED: set(),
}


class BasePlan(TimeStampedModel):
    """Shared header, workflow state and optimistic-locking behaviour."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE)
    template_version = models.ForeignKey(
        "planning.TemplateVersion",
        on_delete=models.PROTECT,
        help_text="The exact version this plan was authored against.",
    )
    assignment = models.ForeignKey(
        "schools.TeacherAssignment",
        on_delete=models.PROTECT,
        help_text="Binds the plan to one teacher, subject and class.",
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=20, choices=PlanState.choices, default=PlanState.DRAFT)

    # 8.5: autosave uses revision tokens; conflicting edits must be resolved.
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True

    @property
    def is_editable(self):
        return self.state in EDITABLE_STATES

    @property
    def is_locked(self):
        """Approved plans are immutable; changes must create a revision (7.5)."""
        return self.state in {PlanState.APPROVED, PlanState.ARCHIVED}

    @property
    def is_pending_review(self):
        return self.state in PENDING_STATES

    def can_transition_to(self, state):
        return state in ALLOWED_TRANSITIONS.get(self.state, set())

    def clean(self):
        super().clean()
        if self.assignment_id and self.assignment.school_id != self.school_id:
            raise ValidationError({"assignment": "The assignment belongs to another school."})
        if self.template_version_id and self.template_version.school_id != self.school_id:
            raise ValidationError({"template_version": "That template is from another school."})


class WorkPlan(BasePlan):
    """Semester Work Plan header (7.2)."""

    academic_year = models.ForeignKey(
        "schools.AcademicYear", on_delete=models.PROTECT, related_name="work_plans"
    )
    term = models.ForeignKey("schools.Term", on_delete=models.PROTECT, related_name="work_plans")
    scheme = models.ForeignKey(
        "curriculum.SchemeOfWork",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_plans",
    )
    resources = models.TextField(blank=True, help_text="WP-T02, page three resources area.")

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "term"),
                condition=~models.Q(state="archived"),
                name="one_active_work_plan_per_assignment_term",
            )
        ]
        indexes = [models.Index(fields=("school", "state", "author"))]

    def clean(self):
        super().clean()
        if self.term_id and self.academic_year_id:
            if self.term.academic_year_id != self.academic_year_id:
                raise ValidationError({"term": "The term must belong to the academic year."})

    @property
    def title(self):
        return (
            f"{self.assignment.subject.name} · {self.assignment.school_class.name} · "
            f"{self.term.name}"
        )

    def __str__(self):
        return self.title


class WorkPlanRow(TimeStampedModel):
    """One calendar-week row of a Work Plan (7.2, WP-D06 to WP-T01)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE)
    work_plan = models.ForeignKey(WorkPlan, on_delete=models.CASCADE, related_name="rows")
    calendar_week = models.ForeignKey(
        "schools.CalendarWeek", on_delete=models.PROTECT, related_name="work_plan_rows"
    )

    # Week identity is copied so an approved plan keeps its printed labels.
    week_number = models.PositiveSmallIntegerField()
    month_label = models.CharField(max_length=40, blank=True)
    week_label = models.CharField(max_length=80, blank=True)
    event_label = models.CharField(max_length=120, blank=True)

    topic = models.ForeignKey(
        "curriculum.Topic", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    subtopic = models.ForeignKey(
        "curriculum.Subtopic", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    learning_objectives = models.ManyToManyField(
        "curriculum.LearningObjective", blank=True, related_name="work_plan_rows"
    )
    #: Human-readable snapshot so historical labels survive curriculum edits.
    objective_labels = models.JSONField(default=list, blank=True)
    remarks = models.TextField(blank=True, help_text="WP-T01, per week.")

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("work_plan", "week_number")
        constraints = [
            models.UniqueConstraint(
                fields=("work_plan", "week_number"), name="unique_work_plan_week"
            )
        ]

    @property
    def is_special_week(self):
        return bool(self.event_label)

    def clean(self):
        super().clean()
        if self.work_plan_id and self.work_plan.school_id != self.school_id:
            raise ValidationError("The work plan belongs to another school.")

    def __str__(self):
        return f"{self.work_plan_id} · Week {self.week_number}"


class LessonPlan(BasePlan):
    """One lesson, authored against the verified LP field map (7.3)."""

    work_plan_row = models.ForeignKey(
        WorkPlanRow,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lesson_plans",
        help_text="Set when the lesson was created from a Work Plan row.",
    )
    lesson_date = models.DateField(help_text="LP-S03, validated against term dates.")

    # LP-D01 cascading unit / sub-unit.
    topic = models.ForeignKey(
        "curriculum.Topic", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    subtopic = models.ForeignKey(
        "curriculum.Subtopic", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # LP-D04 objectives, with a label snapshot for reproducibility.
    learning_objectives = models.ManyToManyField(
        "curriculum.LearningObjective", blank=True, related_name="lesson_plans"
    )
    objective_labels = models.JSONField(default=list, blank=True)

    # LP-D02 / LP-D03 attendance. Null until the teacher records them.
    boys_present = models.PositiveIntegerField(null=True, blank=True)
    girls_present = models.PositiveIntegerField(null=True, blank=True)
    attendance_exception = models.CharField(
        max_length=255,
        blank=True,
        help_text="Audited reason when a count exceeds the roster (8.5).",
    )

    # LP-T01 to LP-T03 free text.
    main_teaching_activity = models.TextField(blank=True)
    assessment_ideas = models.TextField(blank=True)
    notes_remarks = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-lesson_date", "-created_at")
        indexes = [
            models.Index(fields=("school", "state", "author")),
            models.Index(fields=("school", "lesson_date")),
        ]

    @property
    def attendance_total(self):
        """LP-S04, computed as LP-D02 + LP-D03."""
        if self.boys_present is None and self.girls_present is None:
            return None
        return (self.boys_present or 0) + (self.girls_present or 0)

    @property
    def subject(self):
        """LP-S02, read-only from the assignment."""
        return self.assignment.subject

    @property
    def title(self):
        return f"{self.assignment.subject.name} · {self.lesson_date:%d %b %Y}"

    def __str__(self):
        return self.title


class PlanReview(TimeStampedModel):
    """Immutable workflow transition record (7.5).

    Every transition records actor, timestamp, previous state, new state and
    comment. Rows are append-only.
    """

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        CLAIMED = "claimed", "Started review"
        RETURNED = "returned", "Returned"
        RESUBMITTED = "resubmitted", "Resubmitted"
        APPROVED = "approved", "Approved"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE)
    plan_type = models.CharField(max_length=20)
    plan_id = models.UUIDField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    previous_state = models.CharField(max_length=20)
    new_state = models.CharField(max_length=20)
    comment = models.TextField(blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("school", "plan_type", "plan_id"))]

    def save(self, *args, **kwargs):
        if self.pk and PlanReview.all_objects.filter(pk=self.pk).exists():
            raise ValidationError("Review records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review records are immutable.")

    def __str__(self):
        return f"{self.get_action_display()} · {self.created_at:%Y-%m-%d}"


class GeneratedDocument(TimeStampedModel):
    """A rendered PDF with its checksum and verification code (8.6, 11)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE)
    plan_type = models.CharField(max_length=20)
    plan_id = models.UUIDField()
    plan_revision = models.PositiveIntegerField(default=1)
    template_version = models.ForeignKey(
        "planning.TemplateVersion", on_delete=models.PROTECT, related_name="documents"
    )
    plan_state = models.CharField(max_length=20, blank=True)
    file_name = models.CharField(max_length=255)
    checksum = models.CharField(max_length=64)
    byte_size = models.PositiveIntegerField(default=0)
    verification_code = models.CharField(max_length=12, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-generated_at",)
        indexes = [models.Index(fields=("school", "plan_type", "plan_id"))]

    def __str__(self):
        return f"{self.file_name} ({self.checksum[:8]})"


class SyncOperation(TimeStampedModel):
    """Idempotent offline operation queue entry (10.3, 11)."""

    class Result(models.TextChoices):
        APPLIED = "applied", "Applied"
        DUPLICATE = "duplicate", "Duplicate"
        CONFLICT = "conflict", "Conflict"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    operation_id = models.CharField(
        max_length=64, help_text="Client-generated ID making replays idempotent."
    )
    device_id = models.CharField(max_length=64, blank=True)
    plan_type = models.CharField(max_length=20, blank=True)
    plan_id = models.UUIDField(null=True, blank=True)
    base_revision = models.PositiveIntegerField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    result = models.CharField(max_length=20, choices=Result.choices)
    detail = models.JSONField(default=dict, blank=True)

    objects = SchoolScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "operation_id"), name="unique_user_sync_operation"
            )
        ]

    def __str__(self):
        return f"{self.operation_id} · {self.result}"
