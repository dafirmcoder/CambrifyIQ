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
