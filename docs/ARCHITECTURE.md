# CambrifyIQ foundation architecture

## Request flow

```text
Browser / PWA
    │ HTTPS + Django session
    ▼
Django middleware
    ├── authentication
    ├── active school resolution
    └── transaction-local RLS context
    ▼
Views / REST API → service layer → school-scoped managers
    ▼
Supabase PostgreSQL (+ optional RLS backstop)
```

## Tenant boundary

A user may belong to more than one school through `Membership`, but every request has at most one active school. `TenantMiddleware` validates the school against the authenticated user's active memberships and places it in:

- `request.school` and `request.membership`;
- a `ContextVar` used by `SchoolScopedManager`; and
- PostgreSQL transaction-local settings `cams.school_id` and `cams.user_id`.

Tenant-owned models expose a fail-closed `objects` manager. Without an active school it returns no rows. Administrative and migration code must deliberately use `all_objects`; application code should not. A background job must call `.for_school(school)` or enter `tenant_scope(school)`.

Membership is intentionally queried through a global manager because it establishes the active tenant. Every membership query must include the current user or current school. Tests cover invalid school switching and cross-tenant assignment responses.

## Current modules

- `accounts`: email-first custom user and authentication.
- `schools`: schools, memberships, invitations, academic calendar foundation, subjects, classes, assignments and immutable audits.
- `dashboard`: role-aware foundation dashboard and onboarding readiness.
- `api`: session API for login, identity and scoped teacher assignments.
- `core`: middleware, tenant context, health checks and shared web behaviour.

## Roles

| Role | Foundation access |
|---|---|
| Teacher | Dashboard and active assignments only |
| Curriculum Coordinator | School dashboard; curriculum management follows in Phase 1 |
| Head of Cambridge | School settings and team management; cannot appoint/modify a Director |
| School Director | School settings and full team role management |

## Planned bounded contexts

The proposal's remaining modules should be added without weakening this boundary:

1. Curriculum: versioned schemes, topics, subtopics, LOs and AOs.
2. Templates: immutable template versions, fields, options, assets and acceptance state.
3. Planning: work plans, lesson plans and field values.
4. Workflow: reviews, transitions, immutable approved plan revisions and notifications.
5. Documents: protected template assets, deterministic PDF rendering and checksums.
6. Sync: idempotent operations, revision conflicts and per-user offline retention.

The PDF modules must not begin production rendering until the clean Lesson Plan master and Work Plan field decisions are approved.
