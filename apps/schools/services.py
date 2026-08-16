import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from apps.schools.models import AuditLog, Invitation, Membership, School

User = get_user_model()


def _unique_slug(name):
    base = slugify(name)[:170] or "school"
    candidate = base
    counter = 2
    while School.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _school_code(name):
    prefix = "".join(part[0] for part in name.upper().split() if part)[:6] or "SCHOOL"
    while True:
        code = f"{prefix}-{secrets.token_hex(2).upper()}"
        if not School.objects.filter(code=code).exists():
            return code


@transaction.atomic
def register_school(
    *, full_name, email, password1, school_name, leadership_role, password2=None, accept_terms=None
):
    if leadership_role not in {Membership.Role.HEAD, Membership.Role.DIRECTOR}:
        raise ValidationError("The first school member must be a Head or Director.")
    user = User.objects.create_user(email=email, full_name=full_name, password=password1)
    school = School.objects.create(
        name=school_name.strip(),
        slug=_unique_slug(school_name),
        code=_school_code(school_name),
        created_by=user,
    )
    Membership.objects.create(
        school=school,
        user=user,
        role=leadership_role,
        status=Membership.Status.ACTIVE,
        is_primary=True,
    )
    AuditLog.all_objects.create(
        school=school,
        actor=user,
        action="school.created",
        target_type="school",
        target_id=str(school.pk),
        metadata={"role": leadership_role},
    )
    return user, school


def assert_can_manage_users(actor_membership, requested_role=None):
    if not actor_membership or not actor_membership.can_manage_users:
        raise PermissionDenied("Only a Head of Cambridge or School Director can manage users.")
    if (
        requested_role == Membership.Role.DIRECTOR
        and actor_membership.role != Membership.Role.DIRECTOR
    ):
        raise PermissionDenied("Only a School Director can appoint another Director.")


@transaction.atomic
def invite_staff(*, actor_membership, email, role, request=None):
    assert_can_manage_users(actor_membership, role)
    school = actor_membership.school
    email = email.strip().lower()
    if Membership.objects.filter(school=school, user__email__iexact=email).exists():
        raise ValidationError("This person is already a member of the school.")

    raw_token = secrets.token_urlsafe(32)
    token_hash = Invitation.hash_token(raw_token)
    Invitation.all_objects.filter(
        school=school, email__iexact=email, status=Invitation.Status.PENDING
    ).update(status=Invitation.Status.REVOKED)
    invitation = Invitation.all_objects.create(
        school=school,
        email=email,
        role=role,
        token_hash=token_hash,
        invited_by=actor_membership.user,
        expires_at=timezone.now() + timedelta(days=7),
    )
    AuditLog.all_objects.create(
        school=school,
        actor=actor_membership.user,
        action="membership.invited",
        target_type="invitation",
        target_id=str(invitation.pk),
        metadata={"email": email, "role": role},
    )

    accept_url = f"{settings.APP_URL}/school/invitations/{raw_token}/accept/"
    context = {"invitation": invitation, "accept_url": accept_url}
    subject = f"Join {school.name} on CambrifyIQ"
    body = render_to_string("emails/school_invitation.txt", context)
    transaction.on_commit(
        lambda: send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    )
    return invitation, raw_token


def find_invitation(raw_token):
    token_hash = Invitation.hash_token(raw_token)
    return (
        Invitation.all_objects.select_related("school", "invited_by")
        .filter(token_hash=token_hash)
        .first()
    )


@transaction.atomic
def accept_invitation(*, invitation, user):
    invitation = Invitation.all_objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_usable:
        raise ValidationError("This invitation is no longer valid.")
    if invitation.email.lower() != user.email.lower():
        raise PermissionDenied("Sign in with the email address that received this invitation.")
    membership, created = Membership.objects.update_or_create(
        school=invitation.school,
        user=user,
        defaults={"role": invitation.role, "status": Membership.Status.ACTIVE},
    )
    invitation.status = Invitation.Status.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=("status", "accepted_at", "updated_at"))
    AuditLog.all_objects.create(
        school=invitation.school,
        actor=user,
        action="membership.joined",
        target_type="membership",
        target_id=str(membership.pk),
        metadata={"role": membership.role, "invitation_id": str(invitation.pk)},
    )
    return membership


@transaction.atomic
def update_member(*, actor_membership, membership, role, status):
    assert_can_manage_users(actor_membership, role)
    if membership.school_id != actor_membership.school_id:
        raise PermissionDenied("That member is outside your school.")
    if membership.user_id == actor_membership.user_id:
        raise ValidationError("You cannot change your own access from this page.")
    if (
        actor_membership.role == Membership.Role.HEAD
        and membership.role == Membership.Role.DIRECTOR
    ):
        raise PermissionDenied("A Head cannot modify a Director account.")

    previous = {"role": membership.role, "status": membership.status}
    membership.role = role
    membership.status = status
    membership.save(update_fields=("role", "status", "updated_at"))
    AuditLog.all_objects.create(
        school=membership.school,
        actor=actor_membership.user,
        action="membership.updated",
        target_type="membership",
        target_id=str(membership.pk),
        metadata={"previous": previous, "role": role, "status": status},
    )
    return membership
