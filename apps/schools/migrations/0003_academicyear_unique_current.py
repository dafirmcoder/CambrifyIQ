"""
Enforce one current academic year per school at the DB level using a partial
unique index. Requires SQLite >= 3.25 or PostgreSQL.
"""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("schools", "0002_calendarweek"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="academicyear",
            constraint=models.UniqueConstraint(
                fields=("school",),
                condition=Q(is_current=True),
                name="unique_current_academic_year_per_school",
            ),
        ),
    ]
