"""Academic calendar week generation (plan 7.2 and 8.3).

The Semester Work Plan renders one row per teaching week. Weeks are generated
from the term's date range so the received three-page sample — weeks 1 to 17
across August to December — is reproducible for any term the school configures.

The fixed events for weeks 15 to 17 are preserved from the approved sample.
Whether those weeks stay read-only is an owner decision (section 8.3), so the
labels are applied here but the rows remain ordinary editable records.
"""

from datetime import timedelta

from django.db import transaction

from apps.planning.register import WORK_PLAN_SPECIAL_EVENTS
from apps.schools.models import CalendarWeek

#: A teaching week runs Monday to Friday.
WEEK_LENGTH_DAYS = 7
TEACHING_DAYS = 4  # Monday + 4 = Friday.
#: The approved Work Plan sample runs to 17 weeks, ending with the PTC week.
#: A term's raw date range normally spans more calendar weeks than that once
#: holidays are excluded, so generation is capped by default.
DEFAULT_SEMESTER_WEEKS = 17


def week_starts(term):
    """Yield each Monday on or after the term start, up to the term end."""
    cursor = term.starts_on - timedelta(days=term.starts_on.weekday())
    if cursor < term.starts_on:
        cursor += timedelta(days=WEEK_LENGTH_DAYS)
    while cursor <= term.ends_on:
        yield cursor
        cursor += timedelta(days=WEEK_LENGTH_DAYS)


def build_week_rows(term, special_events=None, max_weeks=DEFAULT_SEMESTER_WEEKS):
    """Return the week definitions for a term without writing anything.

    ``max_weeks`` caps the sequence, which matters because a term's raw date
    range usually spans more calendar weeks than it has teaching weeks once
    school holidays are excluded. The approved sample runs to 17.
    """
    events = WORK_PLAN_SPECIAL_EVENTS if special_events is None else special_events
    rows = []
    for index, monday in enumerate(week_starts(term), start=1):
        if max_weeks and index > max_weeks:
            break
        friday = min(monday + timedelta(days=TEACHING_DAYS), term.ends_on)
        event = events.get(index, "")
        rows.append(
            {
                "number": index,
                "starts_on": monday,
                "ends_on": friday,
                "month_label": monday.strftime("%B"),
                "event_label": event,
                "is_teaching_week": not event,
            }
        )
    return rows


@transaction.atomic
def generate_weeks(term, *, special_events=None, replace=False, max_weeks=DEFAULT_SEMESTER_WEEKS):
    """Create the ``CalendarWeek`` rows for a term.

    Existing weeks are left untouched unless ``replace`` is set, so an approved
    Work Plan never loses the week rows it referenced.
    """
    existing = CalendarWeek.all_objects.filter(term=term)
    if replace:
        existing.delete()
    elif existing.exists():
        return list(existing.order_by("number"))

    weeks = [
        CalendarWeek(school_id=term.school_id, term=term, **row)
        for row in build_week_rows(term, special_events, max_weeks)
    ]
    CalendarWeek.all_objects.bulk_create(weeks)
    return list(CalendarWeek.all_objects.filter(term=term).order_by("number"))
