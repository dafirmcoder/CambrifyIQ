def tenant_context(request):
    return {
        "active_school": getattr(request, "school", None),
        "active_membership": getattr(request, "membership", None),
        "school_memberships": getattr(request, "school_memberships", ()),
    }
