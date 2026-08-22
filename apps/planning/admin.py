from django.contrib import admin

from apps.planning.models import (
    PlanningTemplate,
    TemplateField,
    TemplateFieldOption,
    TemplateVersion,
    WorkPlan,
    WorkPlanEvent,
    WorkPlanWeek,
    WorkPlanWeekObjective,
)


@admin.register(PlanningTemplate)
class PlanningTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "template_type", "is_active")
    list_filter = ("school", "template_type", "is_active")
    search_fields = ("name", "school__name")


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    list_display = ("template", "version", "school", "status", "effective_from")
    list_filter = ("school", "status")


admin.site.register(
    [
        TemplateField,
        TemplateFieldOption,
        WorkPlan,
        WorkPlanEvent,
        WorkPlanWeek,
        WorkPlanWeekObjective,
    ]
)
