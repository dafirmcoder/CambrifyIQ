from django import forms

from apps.curriculum.models import SchemeOfWork, Topic
from apps.planning.models import WorkPlanWeek
from apps.schools.models import AcademicYear, TeacherAssignment, Term


class WorkPlanCreateForm(forms.Form):
    assignment = forms.ModelChoiceField(queryset=TeacherAssignment.objects.none())
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none())
    term = forms.ModelChoiceField(queryset=Term.objects.none())
    scheme = forms.ModelChoiceField(queryset=SchemeOfWork.objects.none())

    def __init__(self, *args, school, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignment"].queryset = TeacherAssignment.objects.filter(
            teacher=user, is_active=True
        ).select_related("subject", "school_class")
        self.fields["academic_year"].queryset = AcademicYear.objects.all()
        self.fields["term"].queryset = Term.objects.filter(is_active=True).select_related("academic_year")
        self.fields["scheme"].queryset = SchemeOfWork.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        assignment = cleaned.get("assignment")
        scheme = cleaned.get("scheme")
        if assignment and scheme:
            subject_code = assignment.subject.cambridge_code or assignment.subject.code
            if scheme.subject_code != subject_code or (
                assignment.school_class.year_group
                and scheme.year_group != assignment.school_class.year_group
            ):
                self.add_error("scheme", "Choose the scheme for this assignment's subject and year group.")
        return cleaned


class LessonPlanCreateForm(forms.Form):
    assignment = forms.ModelChoiceField(queryset=TeacherAssignment.objects.none())
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none())
    term = forms.ModelChoiceField(queryset=Term.objects.none())
    scheme = forms.ModelChoiceField(queryset=SchemeOfWork.objects.none())
    lesson_date = forms.DateField(required=True)
    topic = forms.ModelChoiceField(queryset=Topic.objects.none(), required=True)
    originating_work_plan_week = forms.ModelChoiceField(
        queryset=WorkPlanWeek.objects.none(), required=False, label="Carry forward from Work Plan week"
    )

    def __init__(self, *args, school, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignment"].queryset = TeacherAssignment.objects.filter(
            teacher=user, is_active=True
        ).select_related("subject", "school_class")
        self.fields["academic_year"].queryset = AcademicYear.objects.filter(school=school)
        self.fields["term"].queryset = Term.objects.filter(school=school, is_active=True).select_related(
            "academic_year"
        )
        self.fields["scheme"].queryset = SchemeOfWork.objects.filter(is_active=True)
        self.fields["topic"].queryset = Topic.objects.filter(scheme__is_active=True)
        self.fields["originating_work_plan_week"].queryset = WorkPlanWeek.objects.filter(
            work_plan__assignment__teacher=user, school=school
        ).select_related("work_plan", "work_plan__assignment", "work_plan__term")

    def clean(self):
        cleaned = super().clean()
        assignment = cleaned.get("assignment")
        term = cleaned.get("term")
        scheme = cleaned.get("scheme")
        topic = cleaned.get("topic")
        origin = cleaned.get("originating_work_plan_week")

        if assignment and scheme:
            subject_code = assignment.subject.cambridge_code or assignment.subject.code
            if scheme.subject_code != subject_code or (
                assignment.school_class.year_group
                and scheme.year_group != assignment.school_class.year_group
            ):
                self.add_error("scheme", "Choose the scheme for this assignment's subject and year group.")
        if term and assignment and term.school_id != assignment.school_id:
            self.add_error("term", "Choose a term from this teacher's school.")
        if topic and scheme and topic.scheme_id != scheme.pk:
            self.add_error("topic", "Choose a topic from the selected scheme.")
        if origin and assignment and origin.work_plan.assignment_id != assignment.pk:
            self.add_error(
                "originating_work_plan_week",
                "Carry forwarding only works when the Work Plan row belongs to this assignment.",
            )
        if origin and term and origin.work_plan.term_id != term.pk:
            self.add_error("originating_work_plan_week", "Select a Work Plan week from the chosen term.")
        return cleaned
