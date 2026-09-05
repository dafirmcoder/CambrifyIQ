from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

from apps.schools.models import AcademicYear, Membership, School, SchoolClass, Subject, Term

User = get_user_model()

WEEK_START_CHOICES = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]


class SchoolSettingsForm(forms.ModelForm):
    logo_file = forms.ImageField(
        required=False,
        label="Upload School Logo",
        help_text="Upload official school crest or logo (PNG with transparent background recommended, max 2MB).",
    )

    class Meta:
        model = School
        fields = ("name", "timezone", "country", "address", "phone", "website", "logo_url")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"logo_url": "Or specify a direct logo URL."}


class InvitationForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    role = forms.ChoiceField(choices=Membership.Role.choices)


class MemberUpdateForm(forms.Form):
    role = forms.ChoiceField(choices=Membership.Role.choices)
    status = forms.ChoiceField(choices=Membership.Status.choices)


class InvitationAccountForm(forms.Form):
    full_name = forms.CharField(max_length=150, label="Your full name")
    password1 = forms.CharField(
        label="Create password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, email, **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        if password1 and password1 != cleaned.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if password1:
            candidate = User(email=self.email, full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(password1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class EmptyConfirmForm(forms.Form):
    confirm = forms.BooleanField(widget=forms.HiddenInput, initial=True, required=False)


# ── Academic-setup forms ──────────────────────────────────────────────────────


class AcademicYearForm(forms.ModelForm):
    """Create or edit an AcademicYear.  school is injected by the view."""

    class Meta:
        model = AcademicYear
        fields = ("name", "starts_on", "ends_on", "is_current")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        help_texts = {
            "name": "e.g. 2026/2027",
            "is_current": (
                "Only one year can be current at a time.  "
                "Setting this will clear the flag on all other years."
            ),
        }

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        self._school = school

    def clean(self):
        cleaned = super().clean()
        starts_on = cleaned.get("starts_on")
        ends_on = cleaned.get("ends_on")
        if starts_on and ends_on:
            if ends_on <= starts_on:
                raise ValidationError({"ends_on": "The end date must be after the start date."})
            # Overlap check against siblings
            qs = AcademicYear.all_objects.filter(school=self._school)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            overlapping = qs.filter(starts_on__lt=ends_on, ends_on__gt=starts_on)
            if overlapping.exists():
                other = overlapping.first()
                raise ValidationError(
                    f"These dates overlap with '{other.name}' "
                    f"({other.starts_on} – {other.ends_on})."
                )
        return cleaned


class TermForm(forms.ModelForm):
    """Create or edit a Term nested under an AcademicYear."""

    class Meta:
        model = Term
        fields = ("name", "sequence", "starts_on", "ends_on", "is_active")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, academic_year, **kwargs):
        super().__init__(*args, **kwargs)
        self._year = academic_year

    def clean(self):
        cleaned = super().clean()
        starts_on = cleaned.get("starts_on")
        ends_on = cleaned.get("ends_on")
        if starts_on and ends_on:
            if ends_on <= starts_on:
                raise ValidationError({"ends_on": "The end date must be after the start date."})
            # Must sit inside the academic year
            if starts_on < self._year.starts_on:
                raise ValidationError(
                    {
                        "starts_on": f"Term cannot start before the academic year "
                        f"({self._year.starts_on})."
                    }
                )
            if ends_on > self._year.ends_on:
                raise ValidationError(
                    {"ends_on": f"Term cannot end after the academic year ({self._year.ends_on})."}
                )
            # Sibling overlap check
            qs = Term.all_objects.filter(academic_year=self._year)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            overlapping = qs.filter(starts_on__lt=ends_on, ends_on__gt=starts_on)
            if overlapping.exists():
                other = overlapping.first()
                raise ValidationError(
                    f"These dates overlap with term '{other.name}' "
                    f"({other.starts_on} – {other.ends_on})."
                )
        return cleaned


class GenerateWeeksForm(forms.Form):
    """Parameters for the automatic week-generation action."""

    week_start_day = forms.ChoiceField(
        choices=WEEK_START_CHOICES,
        initial=0,
        label="Week starts on",
    )

    def clean_week_start_day(self):
        return int(self.cleaned_data["week_start_day"])


class CloneCalendarForm(forms.Form):
    """Copy a term's week structure into another term."""

    target_term = forms.ModelChoiceField(
        queryset=Term.all_objects.none(),
        label="Copy weeks into",
        help_text=(
            "Weeks will be date-shifted to fit the target term. "
            "Existing weeks in the target are left unchanged."
        ),
        empty_label="— choose a term —",
    )

    def __init__(self, *args, available_terms, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_term"].queryset = available_terms


class SubjectForm(forms.ModelForm):
    """Create or edit a Subject, with a cambridge_code dropdown from live schemes."""

    class Meta:
        model = Subject
        fields = ("name", "code", "cambridge_code", "is_active")
        help_texts = {
            "cambridge_code": (
                "Must match a Cambridge subject code in the curriculum database "
                "to enable Work Plan creation."
            ),
        }

    def __init__(self, *args, cambridge_codes=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cambridge_codes:
            choices = [("", "— not mapped —")] + [(c, c) for c in sorted(cambridge_codes)]
            self.fields["cambridge_code"] = forms.ChoiceField(
                choices=choices,
                required=False,
                label="Cambridge subject code",
                help_text=self.fields["cambridge_code"].help_text,
            )


class SchoolClassForm(forms.ModelForm):
    """Create or edit a SchoolClass, with a year_group dropdown from live schemes."""

    class Meta:
        model = SchoolClass
        fields = ("name", "year_group", "boys_count", "girls_count", "is_active")
        help_texts = {
            "year_group": (
                "Must match a year group in the curriculum database to enable Work Plan creation."
            ),
        }

    def __init__(self, *args, year_groups=None, **kwargs):
        super().__init__(*args, **kwargs)
        if year_groups:
            choices = [("", "— not mapped —")] + [(y, y) for y in sorted(year_groups)]
            self.fields["year_group"] = forms.ChoiceField(
                choices=choices,
                required=False,
                label="Year group",
                help_text=self.fields["year_group"].help_text,
            )


class TeacherAssignmentForm(forms.Form):
    """Create or edit a TeacherAssignment."""

    teacher = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Teacher",
        empty_label="— choose a teacher —",
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.all_objects.none(),
        label="Subject",
        empty_label="— choose a subject —",
    )
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.all_objects.none(),
        label="Class",
        empty_label="— choose a class —",
    )
    effective_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    effective_until = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Leave blank for open-ended assignments.",
    )
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        self._school = school
        self.fields["teacher"].queryset = User.objects.filter(
            memberships__school=school,
            memberships__role=Membership.Role.TEACHER,
            memberships__status=Membership.Status.ACTIVE,
        ).distinct()
        self.fields["subject"].queryset = Subject.all_objects.filter(school=school, is_active=True)
        self.fields["school_class"].queryset = SchoolClass.all_objects.filter(
            school=school, is_active=True
        )

    def clean(self):
        cleaned = super().clean()
        effective_from = cleaned.get("effective_from")
        effective_until = cleaned.get("effective_until")
        if effective_from and effective_until and effective_until < effective_from:
            raise ValidationError(
                {"effective_until": "The end date must be on or after the start date."}
            )
