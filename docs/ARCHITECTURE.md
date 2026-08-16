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
- `curriculum`: versioned schemes of work, topics, subtopics, learning and assessment objectives, scoped option services and validated imports.
- `planning`: immutable planning templates, template versions, the verified RED/BLUE/system field register and the template locking procedure.
- `dashboard`: role-aware foundation dashboard and onboarding readiness.
- `api`: session API for login, identity and scoped teacher assignments.
- `core`: middleware, tenant context, health checks and shared web behaviour.

## Roles

| Role | Foundation access |
|---|---|
| Teacher | Dashboard and active assignments only |
| Curriculum Coordinator | School dashboard, curriculum content editing and imports; may propose a template version |
| Head of Cambridge | School settings and team management; cannot appoint/modify a Director |
| School Director | School settings and full team role management |

## Planned bounded contexts

The proposal's remaining modules should be added without weakening this boundary:

1. ~~Curriculum: versioned schemes, topics, subtopics, LOs and AOs.~~ Delivered.
2. ~~Templates: immutable template versions, fields, options, assets and acceptance state.~~ Delivered.
3. Planning: work plans, lesson plans and field values.
4. Workflow: reviews, transitions, immutable approved plan revisions and notifications.
5. Documents: protected template assets, deterministic PDF rendering and checksums.
6. Sync: idempotent operations, revision conflicts and per-user offline retention.

The PDF modules must not begin production rendering until the clean Lesson Plan master and Work Plan field decisions are approved.

## Template lockdown

`apps.planning` turns the school's Lesson Plan and Semester Work Plan layouts into
versioned application templates, as required by plan sections 8.1 to 8.8.

`apps/planning/register.py` holds the verified annotation register. It is the single
source of truth used to seed `TemplateField` rows, and the acceptance tests assert its
shape directly: the Lesson Plan has exactly four RED controlled pickers, three BLUE
free-text inputs and five fixed/system entries.

| Colour | Meaning | Enforcement |
|---|---|---|
| RED | Controlled picker, no uncontrolled typing | `TemplateField.clean` rejects text controls and requires an `option_source` |
| BLUE | Teacher-authored free text | Rejects picker controls and read-only flags |
| System | Fixed or read-only context value | Static, computed, date or read-only text only |

### Version lifecycle

```text
draft → in_review → approved → current → superseded
```

Only a draft may be edited. Once a version is approved, `TemplateVersion.save` and
`TemplateField.save` refuse in-place changes so approved plans stay reproducible;
publishing is the single permitted transition on a locked row. Every transition writes
an immutable `AuditLog` entry.

### Production-master constraint

`TEMPLATE.pdf` is a flattened raster whose red and blue circles are part of the image,
so it can never back production output. A version is only `is_renderable` once a clean
unmarked master is attached and approved, and `validate_for_lock` blocks approval until
that happens. `LP-D02` and `LP-D03` remain two separate field IDs even though the
attendance circles touch in the source.

## Curriculum option scoping

Section 10.5 requires that a value which cannot be used in a plan also cannot be
discovered through a dropdown endpoint. Every option list is therefore produced by
`apps/curriculum/services.py`, which intersects the caller's membership and active
`TeacherAssignment` rows with the curriculum data before returning anything.

- Leadership sees all school curriculum; a teacher sees only schemes whose subject
  *and* class match an active assignment.
- Deactivated or expired rows stay readable on historical plans but leave `selectable()`.
- `resolve_selected_objectives` re-validates submitted IDs server-side, so client
  labels are never trusted.
