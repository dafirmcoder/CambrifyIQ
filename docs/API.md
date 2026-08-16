# Foundation API

All routes use JSON. Browser sessions are the only enabled authentication mechanism in the foundation release. Mutating calls made from a browser session must include Django's CSRF token.

## Authentication

### `POST /api/auth/login/`

```json
{"email": "teacher@school.example", "password": "..."}
```

Creates a Django session. The endpoint is throttled to 10 anonymous attempts per minute.

### `POST /api/auth/logout/`

Ends the current session. Authentication and CSRF are required.

## Identity

### `GET /api/me/`

Returns the user, active school/role and all active school memberships.

### `GET /api/me/assignments/`

Returns only currently effective assignments belonging to the signed-in teacher and active school. The server intersects user, tenant, activity and effective dates; it never accepts a client-supplied school ID.

## Curriculum

### `GET /api/schemes/`

Returns published schemes of work the caller may plan against. Leadership sees every
scheme in the active school; a teacher sees only schemes whose subject **and** class both
match one of their currently effective assignments.

### `GET /api/schemes/{id}/objectives/`

Returns the unit, sub-unit and learning-objective tree for one scheme, ready to drive the
cascading `LP-D01` picker and the `LP-D04` / `WP-D08` multi-selects.

```json
{"results": [{"id": "...", "code": "T1", "title": "Forces and motion",
  "subtopics": [{"id": "...", "code": "T1.1", "title": "Speed", "objectives": []}],
  "objectives": [{"id": "...", "code": "8Ps.01", "text": "...", "label": "8Ps.01: ..."}]}]}
```

Requesting a scheme outside the caller's assignments returns `403` and never reveals
whether the record exists.

## Templates

### `GET /api/templates/?type={work_plan|lesson_plan}`

Returns the version currently published for the active school, including whether it is
`renderable` — that is, whether an approved clean master is attached. Returns `404` when
no version has been published yet, and `400` for an unknown type.

### `GET /api/templates/{id}/fields/`

Returns the immutable field map: every field ID with its annotation `kind`
(`red`, `blue` or `system`), control type, required flag, bounds, measured PDF `box` in
points and approved `overflow_policy`. Versions belonging to another school return `404`.

## Errors

Errors have one envelope:

```json
{
  "error": {
    "status": 403,
    "code": "not_authenticated",
    "detail": "Authentication credentials were not provided."
  }
}
```

The curriculum, template, work plan, lesson plan, workflow and sync endpoints from the project proposal are intentionally reserved for their implementation phases.
