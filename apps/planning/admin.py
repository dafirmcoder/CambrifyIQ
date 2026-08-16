from django.contrib import admin

from apps.planning.models import (
    PlanningTemplate,
    TemplateField,
    TemplateFieldOption,
    TemplateVersion,
)


@admin.register(PlanningTemplate)
class PlanningTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "plan_type", "is_active")
    list_filter = ("school", "plan_type", "is_active")


class TemplateFieldInline(admin.TabularInline):
    model = TemplateField
    extra = 0
    fields = ("field_id", "label", "kind", "control", "page", "is_required", "option_source")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    list_display = (
        "template",
        "version",
        "status",
        "clean_master_approved",
        "approved_at",
        "published_at",
    )
    list_filter = ("school", "status", "template__plan_type")
    readonly_fields = ("approved_at", "approved_by", "published_at")
    inlines = [TemplateFieldInline]

    def has_delete_permission(self, request, obj=None):
        # Locked versions must stay reproducible for approved plans.
        return bool(obj and not obj.is_locked)


@admin.register(TemplateField)
class TemplateFieldAdmin(admin.ModelAdmin):
    list_display = ("field_id", "label", "kind", "control", "page", "is_required")
    list_filter = ("school", "kind", "control")
    search_fields = ("field_id", "label")


@admin.register(TemplateFieldOption)
class TemplateFieldOptionAdmin(admin.ModelAdmin):
    list_display = ("field", "label", "value", "sequence", "is_active")
    list_filter = ("school", "is_active")
