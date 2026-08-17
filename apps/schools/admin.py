from django.contrib import admin

from apps.schools.models import (
    AcademicYear,
    AuditLog,
    CalendarWeek,
    Invitation,
    Membership,
    School,
    SchoolClass,
    Subject,
    TeacherAssignment,
    Term,
)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "country", "is_active", "created_at")
    search_fields = ("name", "code")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "school", "role", "status", "is_primary")
    list_filter = ("role", "status", "school")
    search_fields = ("user__email", "user__full_name", "school__name")


@admin.register(TeacherAssignment)
class TeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "school", "subject", "school_class", "effective_from", "is_active")
    list_filter = ("school", "is_active")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "school", "actor", "target_type", "created_at")
    list_filter = ("school", "action")
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([AcademicYear, CalendarWeek, Invitation, SchoolClass, Subject, Term])
