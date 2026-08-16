from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render

from apps.accounts.forms import LoginForm, SchoolRegistrationForm
from apps.schools.services import register_school


class AccountLoginView(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class AccountLogoutView(LogoutView):
    http_method_names = ["post", "options"]


def create_school(request):
    if request.user.is_authenticated and getattr(request, "school", None):
        return redirect("dashboard:home")

    form = SchoolRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user, school = register_school(**form.cleaned_data)
        login(request, user, backend="apps.accounts.backends.EmailBackend")
        request.session["active_school_id"] = str(school.pk)
        messages.success(request, f"Welcome to {school.name}. Your workspace is ready.")
        return redirect("dashboard:home")
    return render(request, "accounts/create_school.html", {"form": form})
