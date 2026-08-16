from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm

from apps.schools.models import Membership

User = get_user_model()


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class SchoolRegistrationForm(forms.Form):
    full_name = forms.CharField(max_length=150, label="Your full name")
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Use at least 8 characters and avoid common passwords.",
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    school_name = forms.CharField(max_length=180, label="School name")
    leadership_role = forms.ChoiceField(
        choices=[
            (Membership.Role.DIRECTOR, "School Director"),
            (Membership.Role.HEAD, "Head of Cambridge"),
        ],
        label="Your role",
    )
    accept_terms = forms.BooleanField(
        label="I confirm I am authorised to create this school workspace"
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email already exists. Please sign in."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")
        if password1:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(password1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned
