from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def school_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "school", None):
            messages.info(request, "Create or join a school to continue.")
            return redirect("accounts:create_school")
        return view_func(request, *args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            membership = getattr(request, "membership", None)
            if not membership or membership.role not in roles:
                raise PermissionDenied("Your school role does not allow this action.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
