from django.contrib import admin

from apps.curriculum.models import (
    AssessmentObjective,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)


class TopicInline(admin.TabularInline):
    model = Topic
    extra = 0
    fields = ("code", "title", "sequence", "suggested_weeks", "is_active")
    show_change_link = True


@admin.register(SchemeOfWork)
class SchemeOfWorkAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "subject", "school_class", "version", "status", "is_active")
    list_filter = ("school", "status", "is_active", "subject")
    search_fields = ("code", "title")
    inlines = [TopicInline]


class SubtopicInline(admin.TabularInline):
    model = Subtopic
    extra = 0
    fields = ("code", "title", "sequence", "is_active")
    show_change_link = True


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "scheme", "sequence", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("code", "title")
    inlines = [SubtopicInline]


@admin.register(Subtopic)
class SubtopicAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "topic", "sequence", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("code", "title")


@admin.register(LearningObjective)
class LearningObjectiveAdmin(admin.ModelAdmin):
    list_display = ("code", "topic", "subtopic", "sequence", "version", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("code", "text")


@admin.register(AssessmentObjective)
class AssessmentObjectiveAdmin(admin.ModelAdmin):
    list_display = ("code", "subject", "sequence", "is_active")
    list_filter = ("school", "is_active", "subject")
    search_fields = ("code", "text")
    filter_horizontal = ("learning_objectives",)
