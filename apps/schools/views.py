from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.schools.forms import (
    InvitationAccountForm,
    InvitationForm,
    MemberUpdateForm,
    SchoolSettingsForm,
)
from apps.schools.models import AuditLog, Invitation, Membership
from apps.schools.services import (
    accept_invitation,
    find_invitation,
    invite_staff,
    update_member,
)

User = get_user_model()


def _require_user_manager(request):
    if not request.membership or not request.membership.can_manage_users:
        raise PermissionDenied("Only school leaders can manage team access.")


@login_required
@school_required
def school_settings(request):
    if not request.membership.can_manage_school:
        raise PermissionDenied("Only a Head or Director can edit school settings.")
    form = SchoolSettingsForm(request.POST or None, instance=request.school)
    if request.method == "POST" and form.is_valid():
        school = form.save(commit=False)
        school.onboarding_complete = True
        school.save()
        AuditLog.all_objects.create(
            school=school,
            actor=request.user,
            action="school.settings_updated",
            target_type="school",
            target_id=str(school.pk),
        )
        messages.success(request, "School details saved.")
        return redirect("schools:settings")
    return render(request, "schools/settings.html", {"form": form})


@login_required
@school_required
def team(request):
    _require_user_manager(request)
    memberships = (
        Membership.objects.select_related("user")
        .filter(school=request.school)
        .order_by("role", "user__full_name")
    )
    invitations = Invitation.objects.filter(status=Invitation.Status.PENDING)
    return render(
        request,
        "schools/team.html",
        {
            "memberships": memberships,
            "invitations": invitations,
            "role_choices": Membership.Role.choices,
        },
    )


@login_required
@school_required
def invite_member(request):
    _require_user_manager(request)
    form = InvitationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            invite_staff(actor_membership=request.membership, request=request, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Invitation sent to {form.cleaned_data['email']}.")
            return redirect("schools:team")
    return render(request, "schools/invite.html", {"form": form})


@login_required
@school_required
@require_POST
def update_member_view(request, membership_id):
    _require_user_manager(request)
    membership = get_object_or_404(Membership, pk=membership_id, school=request.school)
    form = MemberUpdateForm(request.POST)
    if form.is_valid():
        try:
            update_member(
                actor_membership=request.membership, membership=membership, **form.cleaned_data
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Access updated for {membership.user.full_name}.")
    else:
        messages.error(request, "Choose a valid role and account status.")
    return redirect("schools:team")


@login_required
@require_POST
def switch_school(request, school_id):
    membership = get_object_or_404(
        Membership,
        school_id=school_id,
        user=request.user,
        status=Membership.Status.ACTIVE,
        school__is_active=True,
    )
    request.session["active_school_id"] = str(membership.school_id)
    messages.success(request, f"Switched to {membership.school.name}.")
    return redirect(request.POST.get("next") or "dashboard:home")


def accept_invitation_view(request, token):
    invitation = find_invitation(token)
    if invitation is None:
        return render(request, "schools/invitation_invalid.html", status=404)
    if not invitation.is_usable:
        if invitation.status == Invitation.Status.PENDING:
            invitation.status = Invitation.Status.EXPIRED
            invitation.save(update_fields=("status", "updated_at"))
        return render(
            request, "schools/invitation_invalid.html", {"invitation": invitation}, status=410
        )

    existing_user = User.objects.filter(email__iexact=invitation.email).first()
    if request.user.is_authenticated:
        if request.user.email.lower() != invitation.email.lower():
            raise PermissionDenied("This invitation belongs to a different email address.")
        if request.method == "POST":
            try:
                membership = accept_invitation(invitation=invitation, user=request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                request.session["active_school_id"] = str(membership.school_id)
                messages.success(request, f"You joined {membership.school.name}.")
                return redirect("dashboard:home")
        return render(request, "schools/accept_invitation.html", {"invitation": invitation})

    if existing_user:
        next_url = reverse("schools:accept_invitation", kwargs={"token": token})
        login_url = f"{reverse('accounts:login')}?{urlencode({'next': next_url})}"
        return render(
            request,
            "schools/accept_invitation.html",
            {"invitation": invitation, "existing_user": True, "login_url": login_url},
        )

    form = InvitationAccountForm(request.POST or None, email=invitation.email)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(
                email=invitation.email,
                full_name=form.cleaned_data["full_name"],
                password=form.cleaned_data["password1"],
            )
            membership = accept_invitation(invitation=invitation, user=user)
        login(request, user, backend="apps.accounts.backends.EmailBackend")
        request.session["active_school_id"] = str(membership.school_id)
        messages.success(request, f"Welcome to {membership.school.name}.")
        return redirect("dashboard:home")
    return render(
        request,
        "schools/accept_invitation.html",
        {"invitation": invitation, "form": form},
    )
