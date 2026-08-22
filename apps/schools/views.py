import calendar as cal_module
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from apps.core.decorators import school_required
from apps.curriculum.models import SchemeOfWork
from apps.schools.forms import (
    AcademicYearForm,
    CloneCalendarForm,
    GenerateWeeksForm,
    InvitationAccountForm,
    InvitationForm,
    MemberUpdateForm,
    SchoolClassForm,
    SchoolSettingsForm,
    SubjectForm,
    TeacherAssignmentForm,
    TermForm,
)
from apps.schools.models import (
    AcademicYear,
    AuditLog,
    CalendarWeek,
    Invitation,
    Membership,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_school_manager(request):
    """Head or Director only."""
    if not request.membership or not request.membership.can_manage_school:
        raise PermissionDenied("Only a Head or Director can manage school settings.")


def _require_coordinator_or_above(request):
    """Coordinator, Head, or Director."""
    allowed = {
        Membership.Role.COORDINATOR,
        Membership.Role.HEAD,
        Membership.Role.DIRECTOR,
    }
    if not request.membership or request.membership.role not in allowed:
        raise PermissionDenied("Your role does not allow this action.")


def _scheme_dropdowns():
    """Return (cambridge_codes, year_groups) from the live curriculum database."""
    cambridge_codes = list(
        SchemeOfWork.objects.filter(is_active=True)
        .values_list("subject_code", flat=True)
        .distinct()
        .order_by("subject_code")
    )
    year_groups = list(
        SchemeOfWork.objects.filter(is_active=True)
        .values_list("year_group", flat=True)
        .distinct()
        .order_by("year_group")
    )
    return cambridge_codes, year_groups


def _generate_week_rows(term, week_start_day):
    """Return a list of dicts describing generated calendar weeks for the term.

    week_start_day is 0=Monday … 6=Sunday (Python's weekday convention).
    Weeks are clamped to the term bounds.  month_label is 'MONTH' for a week
    entirely within one month or 'MON/MON' when it straddles two months.
    """
    rows = []
    # Find the first week-start-day on or after term.starts_on
    delta = (week_start_day - term.starts_on.weekday()) % 7
    week_start = term.starts_on + timedelta(days=delta)
    if week_start > term.starts_on:
        # There is a partial week at the very beginning — clamp it
        week_start = term.starts_on

    sequence = 1
    cursor = term.starts_on

    # Align cursor to first week boundary
    remainder = (week_start_day - cursor.weekday()) % 7
    cursor = cursor + timedelta(days=remainder) if remainder else cursor

    # Walk week by week
    temp_start = term.starts_on
    while temp_start <= term.ends_on:
        # end of this week = 6 days after temp_start, clamped to term end
        temp_end = min(temp_start + timedelta(days=6), term.ends_on)

        # month_label
        if temp_start.month == temp_end.month:
            label = cal_module.month_name[temp_start.month].upper()
        else:
            label = (
                cal_module.month_abbr[temp_start.month].upper()
                + "/"
                + cal_module.month_abbr[temp_end.month].upper()
            )

        rows.append(
            {
                "sequence": sequence,
                "starts_on": temp_start,
                "ends_on": temp_end,
                "month_label": label,
                "is_instructional": True,
                "event_label": "",
            }
        )
        sequence += 1
        # Move to next week boundary aligned to week_start_day
        temp_start = temp_end + timedelta(days=1)
        remainder = (week_start_day - temp_start.weekday()) % 7
        if remainder and temp_start <= term.ends_on:
            temp_start = temp_start + timedelta(days=remainder)

    return rows


def _in_use_counts(term):
    """Return {calendar_week_id: plan_week_count} for all weeks of the term."""
    from apps.planning.models import WorkPlanWeek

    qs = (
        WorkPlanWeek.all_objects.filter(calendar_week__term=term)
        .values("calendar_week_id")
        .annotate(n=Count("id"))
    )
    return {str(row["calendar_week_id"]): row["n"] for row in qs}


# ── Academic years ────────────────────────────────────────────────────────────


@login_required
@school_required
def academic_years(request):
    _require_school_manager(request)
    school = request.school
    form = AcademicYearForm(request.POST or None, school=school)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            year = form.save(commit=False)
            year.school = school
            if year.is_current:
                AcademicYear.all_objects.filter(school=school, is_current=True).update(
                    is_current=False
                )
            year.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="academic_year.created",
                target_type="academicyear",
                target_id=str(year.pk),
                metadata={"name": year.name},
            )
        messages.success(request, f"Academic year '{year.name}' created.")
        return redirect("schools:academic_years")

    years = (
        AcademicYear.all_objects.filter(school=school)
        .annotate(term_count=Count("terms"))
        .order_by("-starts_on")
    )
    return render(
        request,
        "schools/academic_years.html",
        {"form": form, "years": years},
    )


@login_required
@school_required
def edit_academic_year(request, year_id):
    _require_school_manager(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    form = AcademicYearForm(request.POST or None, instance=year, school=school)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            if updated.is_current:
                AcademicYear.all_objects.filter(school=school, is_current=True).exclude(
                    pk=year.pk
                ).update(is_current=False)
            updated.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="academic_year.updated",
                target_type="academicyear",
                target_id=str(year.pk),
                metadata={"name": updated.name, "is_current": updated.is_current},
            )
        messages.success(request, f"Academic year '{updated.name}' updated.")
        return redirect("schools:academic_years")
    return render(
        request,
        "schools/edit_academic_year.html",
        {"form": form, "year": year},
    )


@login_required
@school_required
@require_POST
def delete_academic_year(request, year_id):
    _require_school_manager(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    term_count = Term.all_objects.filter(academic_year=year).count()
    if term_count > 0:
        messages.error(
            request,
            f"Cannot delete '{year.name}' — it has {term_count} term(s). "
            "Remove or reassign its terms first, or archive the year instead.",
        )
        return redirect("schools:academic_years")
    name = year.name
    year.delete()
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="academic_year.deleted",
        target_type="academicyear",
        target_id=str(year_id),
        metadata={"name": name},
    )
    messages.success(request, f"Academic year '{name}' deleted.")
    return redirect("schools:academic_years")


# ── Terms ─────────────────────────────────────────────────────────────────────


@login_required
@school_required
def terms(request, year_id):
    _require_school_manager(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    form = TermForm(request.POST or None, academic_year=year)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            term = form.save(commit=False)
            term.school = school
            term.academic_year = year
            term.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="term.created",
                target_type="term",
                target_id=str(term.pk),
                metadata={"name": term.name, "year": year.name},
            )
        messages.success(request, f"Term '{term.name}' created.")
        return redirect("schools:terms", year_id=year_id)

    term_list = (
        Term.all_objects.filter(academic_year=year)
        .annotate(week_count=Count("calendar_weeks"))
        .order_by("sequence")
    )
    return render(
        request,
        "schools/terms.html",
        {"form": form, "year": year, "terms": term_list},
    )


@login_required
@school_required
def edit_term(request, year_id, term_id):
    _require_school_manager(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    term = get_object_or_404(Term.all_objects, pk=term_id, school=school, academic_year=year)
    form = TermForm(request.POST or None, instance=term, academic_year=year)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            updated.school = school
            updated.academic_year = year
            updated.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="term.updated",
                target_type="term",
                target_id=str(term.pk),
                metadata={"name": updated.name},
            )
        messages.success(request, f"Term '{updated.name}' updated.")
        return redirect("schools:terms", year_id=year_id)
    return render(
        request,
        "schools/edit_term.html",
        {"form": form, "year": year, "term": term},
    )


@login_required
@school_required
@require_POST
def delete_term(request, year_id, term_id):
    _require_school_manager(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    term = get_object_or_404(Term.all_objects, pk=term_id, school=school, academic_year=year)
    week_count = CalendarWeek.all_objects.filter(term=term).count()
    if week_count > 0:
        messages.error(
            request,
            f"Cannot delete '{term.name}' — it has {week_count} calendar week(s). "
            "Clear the calendar first.",
        )
        return redirect("schools:terms", year_id=year_id)
    name = term.name
    term.delete()
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="term.deleted",
        target_type="term",
        target_id=str(term_id),
        metadata={"name": name, "year": year.name},
    )
    messages.success(request, f"Term '{name}' deleted.")
    return redirect("schools:terms", year_id=year_id)


# ── Calendar weeks ────────────────────────────────────────────────────────────


@login_required
@school_required
def calendar_weeks(request, year_id, term_id):
    _require_coordinator_or_above(request)
    school = request.school
    year = get_object_or_404(AcademicYear.all_objects, pk=year_id, school=school)
    term = get_object_or_404(Term.all_objects, pk=term_id, school=school, academic_year=year)
    is_manager = request.membership.can_manage_school

    generate_form = GenerateWeeksForm(prefix="gen")
    clone_form = None
    preview_rows = None

    # Available target terms for clone (exclude current term)
    other_terms = Term.all_objects.filter(school=school).exclude(pk=term.pk)

    if is_manager:
        clone_form = CloneCalendarForm(prefix="clone", available_terms=other_terms)

    if request.method == "POST":
        action = request.POST.get("action", "")

        # ── Generate preview ──────────────────────────────────────────────────
        if action == "generate_preview":
            if not is_manager:
                raise PermissionDenied("Only a Head or Director can generate weeks.")
            generate_form = GenerateWeeksForm(request.POST, prefix="gen")
            if generate_form.is_valid():
                preview_rows = _generate_week_rows(
                    term, generate_form.cleaned_data["week_start_day"]
                )

        # ── Confirm generation ────────────────────────────────────────────────
        elif action == "confirm_generate":
            if not is_manager:
                raise PermissionDenied("Only a Head or Director can generate weeks.")
            generate_form = GenerateWeeksForm(request.POST, prefix="gen")
            if generate_form.is_valid():
                rows = _generate_week_rows(term, generate_form.cleaned_data["week_start_day"])
                in_use = _in_use_counts(term)
                with transaction.atomic():
                    for row in rows:
                        # Skip weeks already snapshotted into a Work Plan
                        existing = CalendarWeek.all_objects.filter(
                            term=term, sequence=row["sequence"]
                        ).first()
                        if existing and str(existing.pk) in in_use:
                            continue  # protected — do not overwrite
                        if existing:
                            existing.starts_on = row["starts_on"]
                            existing.ends_on = row["ends_on"]
                            existing.month_label = row["month_label"]
                            existing.save(
                                update_fields=[
                                    "starts_on",
                                    "ends_on",
                                    "month_label",
                                    "updated_at",
                                ]
                            )
                        else:
                            CalendarWeek.all_objects.create(
                                school=school,
                                term=term,
                                sequence=row["sequence"],
                                starts_on=row["starts_on"],
                                ends_on=row["ends_on"],
                                month_label=row["month_label"],
                            )
                AuditLog.all_objects.create(
                    school=school,
                    actor=request.user,
                    action="calendar.weeks_generated",
                    target_type="term",
                    target_id=str(term.pk),
                    metadata={"week_count": len(rows)},
                )
                messages.success(request, f"{len(rows)} weeks generated for {term.name}.")
                return redirect("schools:calendar_weeks", year_id=year_id, term_id=term_id)

        # ── Save entire grid ──────────────────────────────────────────────────
        elif action == "save_grid":
            if not is_manager:
                raise PermissionDenied("Only a Head or Director can edit calendar weeks.")
            _handle_save_grid(request, school, term, year_id, term_id)
            return redirect("schools:calendar_weeks", year_id=year_id, term_id=term_id)

        # ── Delete a single week ──────────────────────────────────────────────
        elif action == "delete_week":
            if not is_manager:
                raise PermissionDenied("Only a Head or Director can delete calendar weeks.")
            week_id = request.POST.get("week_id")
            _handle_delete_week(request, school, term, week_id, year_id, term_id)
            return redirect("schools:calendar_weeks", year_id=year_id, term_id=term_id)

        # ── Clone calendar ────────────────────────────────────────────────────
        elif action == "clone_calendar":
            if not is_manager:
                raise PermissionDenied("Only a Head or Director can clone calendar weeks.")
            clone_form = CloneCalendarForm(
                request.POST, prefix="clone", available_terms=other_terms
            )
            if clone_form.is_valid():
                _handle_clone_calendar(request, school, term, clone_form, year_id, term_id)
                return redirect("schools:calendar_weeks", year_id=year_id, term_id=term_id)

    # ── GET or failed POST — render the page ─────────────────────────────────
    in_use = _in_use_counts(term)
    weeks_qs = CalendarWeek.all_objects.filter(term=term).order_by("sequence")
    weeks = [
        {
            "obj": w,
            "in_use_count": in_use.get(str(w.pk), 0),
        }
        for w in weeks_qs
    ]
    return render(
        request,
        "schools/calendar_weeks.html",
        {
            "year": year,
            "term": term,
            "weeks": weeks,
            "generate_form": generate_form,
            "clone_form": clone_form,
            "preview_rows": preview_rows,
            "is_manager": is_manager,
        },
    )


def _handle_save_grid(request, school, term, year_id, term_id):
    """Process the submitted week-grid form (all rows in one POST)."""
    in_use = _in_use_counts(term)
    # Gather week ids from the POST data
    week_ids = request.POST.getlist("week_id")
    any_drift = False

    with transaction.atomic():
        for wid in week_ids:
            try:
                week = CalendarWeek.all_objects.select_for_update().get(
                    pk=wid, term=term, school=school
                )
            except CalendarWeek.DoesNotExist:
                continue

            was_in_use = str(week.pk) in in_use
            prefix = f"week_{wid}"
            is_instructional = request.POST.get(f"{prefix}_is_instructional") == "true"
            event_label = request.POST.get(f"{prefix}_event_label", "").strip()
            month_label = request.POST.get(f"{prefix}_month_label", week.month_label).strip()

            changed = (
                week.is_instructional != is_instructional
                or week.event_label != event_label
                or week.month_label != month_label
            )
            if changed and was_in_use:
                any_drift = True

            week.is_instructional = is_instructional
            if not is_instructional:
                week.event_label = event_label
            else:
                week.event_label = ""
            week.month_label = month_label
            week.save(
                update_fields=[
                    "is_instructional",
                    "event_label",
                    "month_label",
                    "updated_at",
                ]
            )

    if any_drift:
        AuditLog.all_objects.create(
            school=school,
            actor=request.user,
            action="calendar.week_edited_with_snapshots",
            target_type="term",
            target_id=str(term.pk),
            metadata={"warning": "Some edited weeks are already snapshotted in Work Plans."},
        )
        messages.warning(
            request,
            "⚠ Some weeks you edited are already used in Work Plans. "
            "The existing plan snapshots have NOT been changed — teachers will see the "
            "original labels in their plans.",
        )
    else:
        messages.success(request, "Calendar updated.")


def _handle_delete_week(request, school, term, week_id, year_id, term_id):
    """Delete a single CalendarWeek, catching ProtectedError if used by plans."""
    try:
        week = CalendarWeek.all_objects.get(pk=week_id, term=term, school=school)
    except CalendarWeek.DoesNotExist:
        messages.error(request, "Week not found.")
        return
    seq = week.sequence
    try:
        with transaction.atomic():
            week.delete()
            # Compact sequences of remaining weeks
            remaining = CalendarWeek.all_objects.filter(term=term).order_by("sequence")
            for i, w in enumerate(remaining, start=1):
                if w.sequence != i:
                    w.sequence = i
                    w.save(update_fields=["sequence", "updated_at"])
    except ProtectedError:
        count = week.work_plan_weeks.count()
        messages.error(
            request,
            f"Week {seq} is used by {count} Work Plan(s) and cannot be deleted.",
        )
        return
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="calendar.week_deleted",
        target_type="calendarweek",
        target_id=str(week_id),
        metadata={"sequence": seq, "term": str(term.pk)},
    )
    messages.success(request, f"Week {seq} deleted and sequence updated.")


def _handle_clone_calendar(request, school, source_term, clone_form, year_id, term_id):
    """Copy the source term's week structure into the target term, shifting dates."""
    target_term = clone_form.cleaned_data["target_term"]
    if target_term.school_id != school.pk:
        raise PermissionDenied
    source_weeks = CalendarWeek.all_objects.filter(term=source_term).order_by("sequence")
    if not source_weeks.exists():
        messages.warning(request, "This term has no weeks to clone.")
        return

    source_duration = (source_term.ends_on - source_term.starts_on).days
    target_duration = (target_term.ends_on - target_term.starts_on).days
    if source_duration == 0:
        messages.error(request, "Source term has zero duration.")
        return

    scale = target_duration / source_duration
    created = 0
    with transaction.atomic():
        for week in source_weeks:
            # Check if target already has this sequence (do not overwrite)
            if CalendarWeek.all_objects.filter(term=target_term, sequence=week.sequence).exists():
                continue
            # Date-shift proportionally from target start
            rel_start = (week.starts_on - source_term.starts_on).days
            rel_end = (week.ends_on - source_term.starts_on).days
            new_start = target_term.starts_on + timedelta(days=int(rel_start * scale))
            new_end = target_term.starts_on + timedelta(days=int(rel_end * scale))
            new_end = min(new_end, target_term.ends_on)
            CalendarWeek.all_objects.create(
                school=school,
                term=target_term,
                sequence=week.sequence,
                starts_on=new_start,
                ends_on=new_end,
                month_label=week.month_label,
                is_instructional=week.is_instructional,
                event_label=week.event_label,
            )
            created += 1

    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="calendar.cloned",
        target_type="term",
        target_id=str(target_term.pk),
        metadata={"source_term": str(source_term.pk), "weeks_created": created},
    )
    messages.success(request, f"{created} week(s) cloned into '{target_term.name}'.")


# ── Subjects ──────────────────────────────────────────────────────────────────


@login_required
@school_required
def subjects(request):
    _require_coordinator_or_above(request)
    school = request.school
    cambridge_codes, _ = _scheme_dropdowns()
    active_codes = set(cambridge_codes)

    form = SubjectForm(request.POST or None, cambridge_codes=cambridge_codes)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            subj = form.save(commit=False)
            subj.school = school
            subj.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="subject.created",
                target_type="subject",
                target_id=str(subj.pk),
                metadata={"code": subj.code, "cambridge_code": subj.cambridge_code},
            )
        messages.success(request, f"Subject '{subj.name}' created.")
        return redirect("schools:subjects")

    subject_list = Subject.all_objects.filter(school=school).order_by("name")
    subjects_annotated = [
        {
            "obj": s,
            "is_mapped": bool(s.cambridge_code) and s.cambridge_code in active_codes,
        }
        for s in subject_list
    ]
    is_manager = request.membership.can_manage_school
    return render(
        request,
        "schools/subjects.html",
        {
            "form": form,
            "subjects": subjects_annotated,
            "is_manager": is_manager,
        },
    )


@login_required
@school_required
def edit_subject(request, subject_id):
    _require_coordinator_or_above(request)
    school = request.school
    subj = get_object_or_404(Subject.all_objects, pk=subject_id, school=school)
    cambridge_codes, _ = _scheme_dropdowns()
    form = SubjectForm(request.POST or None, instance=subj, cambridge_codes=cambridge_codes)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            updated.school = school
            updated.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="subject.updated",
                target_type="subject",
                target_id=str(subj.pk),
                metadata={"code": updated.code, "cambridge_code": updated.cambridge_code},
            )
        messages.success(request, f"Subject '{updated.name}' updated.")
        return redirect("schools:subjects")
    return render(
        request,
        "schools/edit_subject.html",
        {"form": form, "subject": subj},
    )


@login_required
@school_required
@require_POST
def delete_subject(request, subject_id):
    _require_school_manager(request)
    school = request.school
    subj = get_object_or_404(Subject.all_objects, pk=subject_id, school=school)
    name = subj.name
    try:
        subj.delete()
    except ProtectedError:
        messages.error(
            request,
            f"'{name}' is assigned to one or more teachers and cannot be deleted. "
            "Remove the assignments first.",
        )
        return redirect("schools:subjects")
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="subject.deleted",
        target_type="subject",
        target_id=str(subject_id),
        metadata={"name": name},
    )
    messages.success(request, f"Subject '{name}' deleted.")
    return redirect("schools:subjects")


# ── Classes ───────────────────────────────────────────────────────────────────


@login_required
@school_required
def school_classes(request):
    _require_coordinator_or_above(request)
    school = request.school
    _, year_groups = _scheme_dropdowns()

    form = SchoolClassForm(request.POST or None, year_groups=year_groups)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            klass = form.save(commit=False)
            klass.school = school
            klass.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="class.created",
                target_type="schoolclass",
                target_id=str(klass.pk),
                metadata={"name": klass.name, "year_group": klass.year_group},
            )
        messages.success(request, f"Class '{klass.name}' created.")
        return redirect("schools:school_classes")

    classes = SchoolClass.all_objects.filter(school=school).order_by("name")
    is_manager = request.membership.can_manage_school
    return render(
        request,
        "schools/school_classes.html",
        {"form": form, "classes": classes, "is_manager": is_manager},
    )


@login_required
@school_required
def edit_school_class(request, class_id):
    _require_coordinator_or_above(request)
    school = request.school
    klass = get_object_or_404(SchoolClass.all_objects, pk=class_id, school=school)
    _, year_groups = _scheme_dropdowns()
    form = SchoolClassForm(request.POST or None, instance=klass, year_groups=year_groups)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            updated.school = school
            updated.save()
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="class.updated",
                target_type="schoolclass",
                target_id=str(klass.pk),
                metadata={"name": updated.name},
            )
        messages.success(request, f"Class '{updated.name}' updated.")
        return redirect("schools:school_classes")
    return render(
        request,
        "schools/edit_school_class.html",
        {"form": form, "school_class": klass},
    )


@login_required
@school_required
@require_POST
def delete_school_class(request, class_id):
    _require_school_manager(request)
    school = request.school
    klass = get_object_or_404(SchoolClass.all_objects, pk=class_id, school=school)
    name = klass.name
    try:
        klass.delete()
    except ProtectedError:
        messages.error(
            request,
            f"'{name}' is assigned to one or more teachers and cannot be deleted. "
            "Remove the assignments first.",
        )
        return redirect("schools:school_classes")
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="class.deleted",
        target_type="schoolclass",
        target_id=str(class_id),
        metadata={"name": name},
    )
    messages.success(request, f"Class '{name}' deleted.")
    return redirect("schools:school_classes")


# ── Teaching assignments ──────────────────────────────────────────────────────


@login_required
@school_required
def teaching_assignments(request):
    _require_coordinator_or_above(request)
    school = request.school
    is_manager = request.membership.can_manage_school

    # Filters
    teacher_id = request.GET.get("teacher")
    subject_id = request.GET.get("subject")
    class_id = request.GET.get("class")
    active_only = request.GET.get("active", "1") == "1"

    qs = (
        TeacherAssignment.all_objects.filter(school=school)
        .select_related("teacher", "subject", "school_class")
        .order_by("subject__name", "school_class__name")
    )
    if teacher_id:
        qs = qs.filter(teacher_id=teacher_id)
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if active_only:
        qs = qs.filter(is_active=True)

    teachers = User.objects.filter(
        memberships__school=school,
        memberships__role=Membership.Role.TEACHER,
        memberships__status=Membership.Status.ACTIVE,
    ).distinct()
    all_subjects = Subject.all_objects.filter(school=school, is_active=True)
    all_classes = SchoolClass.all_objects.filter(school=school, is_active=True)

    return render(
        request,
        "schools/teaching_assignments.html",
        {
            "assignments": qs,
            "teachers": teachers,
            "all_subjects": all_subjects,
            "all_classes": all_classes,
            "filter_teacher": teacher_id or "",
            "filter_subject": subject_id or "",
            "filter_class": class_id or "",
            "active_only": active_only,
            "is_manager": is_manager,
        },
    )


@login_required
@school_required
def create_assignment(request):
    _require_coordinator_or_above(request)
    school = request.school
    form = TeacherAssignmentForm(request.POST or None, school=school)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            cd = form.cleaned_data
            assignment = TeacherAssignment(
                school=school,
                teacher=cd["teacher"],
                subject=cd["subject"],
                school_class=cd["school_class"],
                effective_from=cd["effective_from"],
                effective_until=cd.get("effective_until"),
                is_active=cd.get("is_active", True),
            )
            try:
                assignment.full_clean()
                assignment.save()
            except ValidationError as exc:
                form.add_error(None, exc)
                return render(
                    request,
                    "schools/edit_assignment.html",
                    {"form": form, "assignment": None},
                )
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="assignment.created",
                target_type="teacherassignment",
                target_id=str(assignment.pk),
                metadata={
                    "teacher": str(assignment.teacher_id),
                    "subject": assignment.subject.code,
                    "class": assignment.school_class.name,
                },
            )
        messages.success(
            request,
            f"Assignment created: {assignment.teacher.get_short_name()} · "
            f"{assignment.subject.code} · {assignment.school_class.name}.",
        )
        return redirect("schools:teaching_assignments")
    return render(
        request,
        "schools/edit_assignment.html",
        {"form": form, "assignment": None},
    )


@login_required
@school_required
def edit_assignment(request, assignment_id):
    _require_coordinator_or_above(request)
    school = request.school
    assignment = get_object_or_404(TeacherAssignment.all_objects, pk=assignment_id, school=school)
    initial = {
        "teacher": assignment.teacher,
        "subject": assignment.subject,
        "school_class": assignment.school_class,
        "effective_from": assignment.effective_from,
        "effective_until": assignment.effective_until,
        "is_active": assignment.is_active,
    }
    form = TeacherAssignmentForm(request.POST or None, initial=initial, school=school)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            cd = form.cleaned_data
            assignment.teacher = cd["teacher"]
            assignment.subject = cd["subject"]
            assignment.school_class = cd["school_class"]
            assignment.effective_from = cd["effective_from"]
            assignment.effective_until = cd.get("effective_until")
            assignment.is_active = cd.get("is_active", True)
            try:
                assignment.full_clean()
                assignment.save()
            except ValidationError as exc:
                form.add_error(None, exc)
                return render(
                    request,
                    "schools/edit_assignment.html",
                    {"form": form, "assignment": assignment},
                )
            AuditLog.all_objects.create(
                school=school,
                actor=request.user,
                action="assignment.updated",
                target_type="teacherassignment",
                target_id=str(assignment.pk),
                metadata={"subject": assignment.subject.code},
            )
        messages.success(request, "Assignment updated.")
        return redirect("schools:teaching_assignments")
    return render(
        request,
        "schools/edit_assignment.html",
        {"form": form, "assignment": assignment},
    )


@login_required
@school_required
@require_POST
def delete_assignment(request, assignment_id):
    _require_school_manager(request)
    school = request.school
    assignment = get_object_or_404(TeacherAssignment.all_objects, pk=assignment_id, school=school)
    try:
        assignment.delete()
    except ProtectedError:
        messages.error(
            request, "This assignment is referenced by a Work Plan and cannot be deleted."
        )
        return redirect("schools:teaching_assignments")
    AuditLog.all_objects.create(
        school=school,
        actor=request.user,
        action="assignment.deleted",
        target_type="teacherassignment",
        target_id=str(assignment_id),
        metadata={},
    )
    messages.success(request, "Assignment deleted.")
    return redirect("schools:teaching_assignments")
