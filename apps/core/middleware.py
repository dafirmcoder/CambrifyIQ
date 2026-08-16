from django.db import connection, transaction

from apps.core.tenant import reset_current_school, set_current_school


class TenantMiddleware:
    """Resolve the signed-in user's active school and establish tenant context.

    The whole downstream request runs in one transaction. On PostgreSQL this also
    sets transaction-local values consumed by the optional Supabase RLS policies.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.schools.models import Membership

        request.school = None
        request.membership = None
        request.school_memberships = ()
        tenant_token = None

        with transaction.atomic():
            if request.user.is_authenticated:
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config('cams.user_id', %s, true)",
                            [str(request.user.pk)],
                        )

                memberships = list(
                    Membership.objects.select_related("school")
                    .filter(
                        user=request.user, status=Membership.Status.ACTIVE, school__is_active=True
                    )
                    .order_by("-is_primary", "school__name")
                )
                request.school_memberships = memberships
                active_id = request.session.get("active_school_id")
                membership = next((m for m in memberships if str(m.school_id) == active_id), None)
                if membership is None and memberships:
                    membership = memberships[0]
                    request.session["active_school_id"] = str(membership.school_id)

                if membership:
                    request.membership = membership
                    request.school = membership.school
                    tenant_token = set_current_school(membership.school_id)
                    if connection.vendor == "postgresql":
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT set_config('cams.school_id', %s, true)",
                                [str(membership.school_id)],
                            )

            try:
                return self.get_response(request)
            finally:
                if tenant_token is not None:
                    reset_current_school(tenant_token)
