from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import datetime, time, timedelta

from apps.curriculum.models import SchemeOfWork, Topic
from apps.planning.models import LessonPlan, PlanningTemplate, TemplateVersion, WorkPlan, WorkPlanWeek
from apps.schools.models import AcademicYear, SchoolClass, Subject, TeacherAssignment, TeacherScheduleSlot, Term


class LessonPlanCreateForm(forms.Form):
    assignment = forms.ModelChoiceField(
        queryset=TeacherAssignment.objects.none(),
        label="Teaching Assignment",
        widget=forms.Select(attrs={"class": "field select", "id": "id_assignment"}),
    )
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.none(),
        label="Academic Year",
        widget=forms.Select(attrs={"class": "field select", "id": "id_academic_year"}),
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        label="Academic Term",
        widget=forms.Select(attrs={"class": "field select", "id": "id_term"}),
    )
    originating_work_plan_week = forms.ModelChoiceField(
        queryset=WorkPlanWeek.objects.none(),
        label="Work Plan Week (Optional)",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_work_plan_week"}),
    )
    week_number = forms.IntegerField(
        label="Week Number",
        required=False,
        widget=forms.NumberInput(attrs={"class": "field", "id": "id_week_number", "min": 1, "max": 20, "placeholder": "e.g. 2"}),
    )
    schedule_slot = forms.ModelChoiceField(
        queryset=TeacherScheduleSlot.objects.none(),
        label="Weekly Timetable Slot",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_schedule_slot"}),
    )
    lesson_date = forms.DateField(
        label="Lesson Date",
        widget=forms.DateInput(attrs={"class": "field", "type": "date", "id": "id_lesson_date"}),
    )
    start_time = forms.TimeField(
        label="Start Time",
        required=False,
        widget=forms.TimeInput(attrs={"class": "field", "type": "time", "id": "id_start_time"}),
    )
    end_time = forms.TimeField(
        label="End Time",
        required=False,
        widget=forms.TimeInput(attrs={"class": "field", "type": "time", "id": "id_end_time"}),
    )
    topic = forms.ModelChoiceField(
        queryset=Topic.objects.none(),
        label="Topic",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_topic"}),
    )

    def __init__(self, *args, school, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.user = user

        assignments_qs = TeacherAssignment.objects.filter(
            school=school, teacher=user, is_active=True
        ).select_related("subject", "school_class")
        self.fields["assignment"].queryset = assignments_qs

        years_qs = AcademicYear.objects.filter(school=school).order_by("-is_current", "-starts_on")
        self.fields["academic_year"].queryset = years_qs
        active_year = years_qs.filter(is_current=True).first() or years_qs.first()
        if active_year and not self.is_bound:
            self.fields["academic_year"].initial = active_year

        terms_qs = Term.objects.filter(school=school, is_active=True).order_by("-starts_on")
        self.fields["term"].queryset = terms_qs
        active_term = terms_qs.filter(starts_on__lte=timezone.localdate(), ends_on__gte=timezone.localdate()).first() or terms_qs.first()
        if active_term and not self.is_bound:
            self.fields["term"].initial = active_term

        self.fields["originating_work_plan_week"].queryset = (
            WorkPlanWeek.objects.filter(school=school, work_plan__assignment__teacher=user)
            .select_related("work_plan__assignment__subject", "calendar_week", "topic")
            .order_by("sequence")
        )

        self.fields["schedule_slot"].queryset = TeacherScheduleSlot.objects.filter(
            school=school, assignment__teacher=user, is_active=True
        ).select_related("assignment__subject", "assignment__school_class")

        first_ass = assignments_qs.first()
        if first_ass:
            scheme = SchemeOfWork.objects.filter(
                subject_name__icontains=first_ass.subject.name, is_active=True
            ).first()
            if scheme:
                self.fields["topic"].queryset = Topic.objects.filter(scheme=scheme).order_by("sequence")
            else:
                self.fields["topic"].queryset = Topic.objects.all().order_by("sequence")
        else:
            self.fields["topic"].queryset = Topic.objects.all().order_by("sequence")

    def clean(self):
        cleaned_data = super().clean()
        assignment = cleaned_data.get("assignment")
        lesson_date = cleaned_data.get("lesson_date")
        end_time = cleaned_data.get("end_time")

        if assignment:
            scheme = SchemeOfWork.objects.filter(
                subject_name__icontains=assignment.subject.name, is_active=True
            ).first() or SchemeOfWork.objects.filter(is_active=True).first()
            cleaned_data["scheme"] = scheme

        # Prospective Planning Validation
        if lesson_date:
            now = timezone.localtime()
            et = end_time or time(23, 59, 59)
            lesson_end_dt = timezone.make_aware(datetime.combine(lesson_date, et), timezone.get_current_timezone())
            if lesson_end_dt < now:
                raise ValidationError(
                    "Lesson plans cannot be created for past lessons or elapsed times (planning must be prospective)."
                )

        return cleaned_data


class WorkPlanCreateForm(forms.Form):
    framework_tier = forms.CharField(
        label="Curriculum Framework / Tier",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "filter-framework"}),
    )
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.none(),
        label="Class / Year Group",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_wp_school_class"}),
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(),
        label="Subject",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_wp_subject"}),
    )
    assignment = forms.ModelChoiceField(
        queryset=TeacherAssignment.objects.none(),
        label="Teaching Assignment",
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_wp_assignment"}),
    )
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.none(),
        label="Academic Year",
        widget=forms.Select(attrs={"class": "field select", "id": "id_wp_academic_year"}),
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        label="Academic Term",
        widget=forms.Select(attrs={"class": "field select", "id": "id_wp_term"}),
    )
    scheme = forms.ModelChoiceField(
        queryset=SchemeOfWork.objects.none(),
        label="Curriculum Scheme",
        required=False,
        widget=forms.Select(attrs={"class": "field select", "id": "id_wp_scheme"}),
    )

    def __init__(self, *args, school, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.user = user

        self.fields["school_class"].queryset = SchoolClass.objects.filter(school=school).order_by("name")
        self.fields["subject"].queryset = Subject.objects.filter(school=school).order_by("name")

        assignments_qs = TeacherAssignment.objects.filter(
            school=school, teacher=user, is_active=True
        ).select_related("subject", "school_class")
        self.fields["assignment"].queryset = assignments_qs

        years_qs = AcademicYear.objects.filter(school=school).order_by("-is_current", "-starts_on")
        self.fields["academic_year"].queryset = years_qs
        active_year = years_qs.filter(is_current=True).first() or years_qs.first()
        if active_year and not self.is_bound:
            self.fields["academic_year"].initial = active_year

        terms_qs = Term.objects.filter(school=school, is_active=True).order_by("-starts_on")
        self.fields["term"].queryset = terms_qs
        active_term = terms_qs.filter(starts_on__lte=timezone.localdate(), ends_on__gte=timezone.localdate()).first() or terms_qs.first()
        self.fields["scheme"].queryset = SchemeOfWork.objects.filter(is_active=True).select_related("framework").order_by("framework__name", "subject_name", "title")
        self.fields["scheme"].label_from_instance = lambda obj: obj.title or f"{obj.subject_code} {obj.subject_name} {obj.syllabus_years}"

    def clean(self):
        cleaned_data = super().clean()
        assignment = cleaned_data.get("assignment")
        school_class = cleaned_data.get("school_class")
        subject = cleaned_data.get("subject")
        scheme = cleaned_data.get("scheme")

        if assignment:
            if not subject:
                cleaned_data["subject"] = assignment.subject
                subject = assignment.subject
            if not school_class:
                cleaned_data["school_class"] = assignment.school_class
                school_class = assignment.school_class

        if not subject or not school_class:
            raise ValidationError("Please select a Class and Subject for the Work Plan.")

        if not scheme and subject:
            found_scheme = None
            yg = school_class.year_group if school_class else ""
            if yg:
                found_scheme = SchemeOfWork.objects.filter(
                    subject_name__icontains=subject.name, year_group=yg, is_active=True
                ).first()
            if not found_scheme:
                found_scheme = SchemeOfWork.objects.filter(
                    subject_name__icontains=subject.name, is_active=True
                ).first() or SchemeOfWork.objects.filter(is_active=True).first()
            cleaned_data["scheme"] = found_scheme

        return cleaned_data

