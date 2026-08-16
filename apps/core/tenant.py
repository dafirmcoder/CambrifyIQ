"""Request-safe active tenant context.

School-scoped managers fail closed when no tenant is active. Background jobs should
always call ``Model.objects.for_school(school)`` or use ``tenant_scope`` explicitly.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_current_school_id = ContextVar("cambrify_school_id", default=None)


def get_current_school_id():
    return _current_school_id.get()


@contextmanager
def tenant_scope(school_or_id):
    school_id = getattr(school_or_id, "pk", school_or_id)
    token = _current_school_id.set(school_id)
    try:
        yield
    finally:
        _current_school_id.reset(token)


def set_current_school(school_or_id):
    school_id = getattr(school_or_id, "pk", school_or_id)
    return _current_school_id.set(school_id)


def reset_current_school(token):
    _current_school_id.reset(token)
