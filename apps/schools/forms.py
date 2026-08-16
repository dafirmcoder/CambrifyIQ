from django import forms
from django.contrib.auth import get_user_model, password_validation

from apps.schools.models import Membership, School

User = get_user_model()


class SchoolSettingsForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ("name", "timezone", "country", "address", "phone", "website", "logo_url")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"logo_url": "Use a secure, publicly accessible logo URL for now."}


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
