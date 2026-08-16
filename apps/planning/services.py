"""Template locking procedure (CAMS plan 8.7).

The procedure is deliberately gated:

1. Store both uploaded PDFs, checksums and inspection records.
2. The school supplies a clean unmarked master, or approves a recreation.
3. The owner confirms the Work Plan field types from section 8.3.
4. Coordinates, bounds, required flags and overflow rules are refined.
5. Coordinator and Head review; the Director signs the Acceptance Record.
6. Immutable TemplateVersion 1 is published to production.

Every state change is audited, and a locked version can never be edited in place.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.planning.models import (
    PlanningTemplate,
    PlanType,
    TemplateField,
    TemplateFieldOption,
    TemplateVersion,
)
from apps.planning.register import (
    LESSON_PLAN_RESOURCE_PROMPTS,
    SOURCES,
    register_for,
)
from apps.schools.models import AuditLog, Membership

#: Only a Head or Director may approve or publish a template version (6.2).
APPROVER_ROLES = frozenset({Membership.Role.HEAD, Membership.Role.DIRECTOR})
#: Coordinators may propose a draft version; leadership may too.
PROPOSER_ROLES = frozenset(
    {Membership.Role.COORDINATOR, Membership.Role.HEAD, Membership.Role.DIRECTOR}
)


def _require(membership, roles, message):
    if not membership or membership.status != Membership.Status.ACTIVE:
        raise PermissionDenied("An active school membership is required.")
    if membership.role not in roles:
        raise PermissionDenied(message)
    return membership


def _audit(membership, action, version, **metadata):
    AuditLog.all_objects.create(
        school_id=membership.school_id,
        actor_id=membership.user_id,
        action=action,
        target_type="template_version",
        target_id=str(version.pk),
        metadata={
            "plan_type": version.template.plan_type,
            "version": version.version,
            **metadata,
        },
    )


@transaction.atomic
def ensure_template(*, school, plan_type, name=None):
    """Get or create the single PlanningTemplate for a school and plan type."""
    default_name = dict(PlanType.choices)[plan_type]
    template, _ = PlanningTemplate.all_objects.get_or_create(
        school=school,
        plan_type=plan_type,
        defaults={"name": name or default_name},
    )
    return template


@transaction.atomic
def create_draft_version(*, membership, plan_type, seed_register=True, notes=""):
    """Open a new draft version, seeded from the verified annotation register."""
    _require(membership, PROPOSER_ROLES, "Only leadership can propose a template version.")
    template = ensure_template(school=membership.school, plan_type=plan_type)

    latest = TemplateVersion.all_objects.filter(template=template).order_by("-version").first()
    source = SOURCES[plan_type]
    version = TemplateVersion.all_objects.create(
        school=membership.school,
        template=template,
        version=(latest.version + 1) if latest else 1,
        status=TemplateVersion.Status.DRAFT,
        page_count=source["pages"],
        page_width_pt=source["width_pt"],
        page_height_pt=source["height_pt"],
        annotation_source_name=source["filename"],
        annotation_source_sha256=source["sha256"],
        notes=notes,
    )
    if seed_register:
        seed_fields(version=version, plan_type=plan_type)
    _audit(membership, "template.version_drafted", version)
    return version


@transaction.atomic
def seed_fields(*, version, plan_type):
    """Materialise the declared register into TemplateField rows."""
    if version.is_locked:
        raise ValidationError("A locked version cannot be reseeded.")

    created = {}
    for declaration in register_for(plan_type):
        data = dict(declaration)
        box = data.pop("box", None)
        field = TemplateField.all_objects.create(
            school_id=version.school_id,
            template_version=version,
            box_x1=box[0] if box else None,
            box_y1=box[1] if box else None,
            box_x2=box[2] if box else None,
            box_y2=box[3] if box else None,
            **data,
        )
        created[field.field_id] = field

    if plan_type == PlanType.LESSON_PLAN:
        _seed_resource_prompts(version, created)
    return created


def _seed_resource_prompts(version, fields):
    """LP-S05 lists fixed prompts; store them as readable option rows."""
    field = fields.get("LP-S05")
    if not field:
        return
    for index, prompt in enumerate(LESSON_PLAN_RESOURCE_PROMPTS, start=1):
        TemplateFieldOption.all_objects.create(
            school_id=version.school_id,
            field=field,
            value=prompt.lower().replace(" ", "_").replace("'", ""),
            label=prompt,
            sequence=index * 10,
        )


@transaction.atomic
def record_clean_master(*, membership, version, filename, checksum, approved=False):
    """Attach the unmarked production master required by section 2."""
    _require(membership, PROPOSER_ROLES, "Only leadership can attach a template source.")
    if version.is_locked:
        raise ValidationError("A locked version cannot receive a new source file.")
    version.clean_master_name = filename
    version.clean_master_sha256 = checksum
    version.clean_master_approved = approved
    version.save(
        update_fields=(
            "clean_master_name",
            "clean_master_sha256",
            "clean_master_approved",
            "updated_at",
        )
    )
    _audit(
        membership,
        "template.clean_master_recorded",
        version,
        filename=filename,
        approved=approved,
    )
    return version


def validate_for_lock(version):
    """Acceptance criteria that must hold before approval (8.8).

    Returns a list of human-readable blockers; empty means ready to lock.
    """
    blockers = []
    fields = list(version.field_map())
    plan_type = version.template.plan_type

    if not version.is_renderable:
        blockers.append("An approved clean master is required before this version can be locked.")

    declared = {item["field_id"] for item in register_for(plan_type)}
    present = {field.field_id for field in fields}
    if missing := sorted(declared - present):
        blockers.append(f"Missing register fields: {', '.join(missing)}.")
    if extra := sorted(present - declared):
        blockers.append(f"Unregistered fields present: {', '.join(extra)}.")

    if plan_type == PlanType.LESSON_PLAN:
        for field in fields:
            if field.is_controlled and not field.option_source:
                blockers.append(f"{field.field_id} is controlled but has no option source.")
            if (field.is_controlled or field.is_free_text) and field.box is None:
                blockers.append(f"{field.field_id} has no measured placement box.")
            if field.is_free_text and not field.overflow_policy:
                blockers.append(f"{field.field_id} has no approved overflow policy.")
    return blockers


@transaction.atomic
def submit_for_review(*, membership, version):
    _require(membership, PROPOSER_ROLES, "Only leadership can submit a template version.")
    if version.status != TemplateVersion.Status.DRAFT:
        raise ValidationError("Only a draft version can be submitted for review.")
    version.status = TemplateVersion.Status.IN_REVIEW
    version.save(update_fields=("status", "updated_at"))
    _audit(membership, "template.version_submitted", version)
    return version


@transaction.atomic
def approve_version(*, membership, version):
    """Sign the Template Acceptance Record and freeze the definition."""
    _require(membership, APPROVER_ROLES, "Only a Head or Director can approve a template.")
    if version.status not in {TemplateVersion.Status.DRAFT, TemplateVersion.Status.IN_REVIEW}:
        raise ValidationError("Only a draft or in-review version can be approved.")
    if blockers := validate_for_lock(version):
        raise ValidationError(blockers)

    version.status = TemplateVersion.Status.APPROVED
    version.approved_at = timezone.now()
    version.approved_by_id = membership.user_id
    version.save(update_fields=("status", "approved_at", "approved_by", "updated_at"))
    _audit(membership, "template.version_approved", version)
    return version


@transaction.atomic
def publish_version(*, membership, version):
    """Make an approved version current, superseding the previous one."""
    _require(membership, APPROVER_ROLES, "Only a Head or Director can publish a template.")
    if version.status != TemplateVersion.Status.APPROVED:
        raise ValidationError("Only an approved version can become current.")

    previous = (
        TemplateVersion.all_objects.select_for_update()
        .filter(template=version.template, status=TemplateVersion.Status.CURRENT)
        .first()
    )
    if previous:
        previous.status = TemplateVersion.Status.SUPERSEDED
        previous.save(update_fields=("status", "updated_at"))

    version.status = TemplateVersion.Status.CURRENT
    version.published_at = timezone.now()
    version.save(update_fields=("status", "published_at", "updated_at"))
    _audit(
        membership,
        "template.version_published",
        version,
        superseded=previous.version if previous else None,
    )
    return version


def current_version(*, school, plan_type):
    """The version production builders and renderers must use."""
    return (
        TemplateVersion.all_objects.filter(
            school=school,
            template__plan_type=plan_type,
            status=TemplateVersion.Status.CURRENT,
        )
        .select_related("template")
        .first()
    )


def field_map_payload(version):
    """Serialise the field map for the API metadata endpoint (14)."""
    return {
        "template": {
            "id": str(version.template_id),
            "plan_type": version.template.plan_type,
            "name": version.template.name,
        },
        "version": version.version,
        "status": version.status,
        "page_count": version.page_count,
        "page_size_pt": [float(version.page_width_pt), float(version.page_height_pt)],
        "clean_master_approved": version.clean_master_approved,
        "fields": [
            {
                "field_id": field.field_id,
                "label": field.label,
                "kind": field.kind,
                "control": field.control,
                "page": field.page,
                "required": field.is_required,
                "readonly": field.is_readonly,
                "help_text": field.help_text,
                "option_source": field.option_source or None,
                "depends_on": field.depends_on.field_id if field.depends_on_id else None,
                "min_value": field.min_value,
                "max_value": field.max_value,
                "max_length": field.max_length,
                "max_selections": field.max_selections,
                "box": list(field.box) if field.box else None,
                "overflow_policy": field.overflow_policy or None,
            }
            for field in version.field_map()
        ],
    }
