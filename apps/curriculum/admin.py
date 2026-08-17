from django.contrib import admin

from apps.curriculum.models import (
    AssessmentObjective,
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)


@admin.register(CurriculumFramework)
class CurriculumFrameworkAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "publisher", "is_active")
    search_fields = ("code", "name")


@admin.register(SchemeOfWork)
class SchemeOfWorkAdmin(admin.ModelAdmin):
    list_display = ("subject_code", "year_group", "version", "framework", "is_active")
    list_filter = ("framework", "is_active")
    search_fields = ("subject_code", "subject_name", "title")


admin.site.register([Topic, Subtopic, LearningObjective, AssessmentObjective])
