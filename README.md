# CambrifyIQ

CambrifyIQ is a multi-tenant academic planning platform for Cambridge schools. It gives teachers a guided, assignment-scoped planning workspace and gives Curriculum Coordinators, Heads of Cambridge and School Directors controlled oversight.

This repository currently implements the approved **foundation milestone**. Curriculum builders, template locking, workflow and PDF rendering are staged in [`docs/ROADMAP.md`](docs/ROADMAP.md) so they can be built against approved clean templates rather than guessed layouts.

## Foundation features

- Head/Director self-service school registration.
- Email-first Django authentication and four school roles.
- Multi-school memberships with a validated active tenant.
- Leader-managed invitations, acceptance, roles and suspension.
- School profile, academic year, term, subject, class and teacher-assignment models.
- Fail-closed school-scoped managers and transaction-local Postgres tenant context.
- Role-aware dashboard and scoped `/api/me/assignments/` endpoint.
- Immutable audit events and seven-day hashed invitation tokens.
- Responsive, installable PWA shell that does not cache protected pages.
- Supabase Postgres configuration, RLS baseline, Docker and automated tests.

## Stack

- Python 3.12 in production (3.11+ supported)
- Django 5.2 LTS and Django REST Framework
- Supabase managed PostgreSQL via `psycopg`
- WhiteNoise + Gunicorn
- Vanilla responsive UI and service worker

## Quick start

```bash
git clone <repository-url>
cd CambrifyIQ
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

For a zero-configuration local run, remove/comment `DATABASE_URL` in `.env` to use SQLite. To use Supabase, replace `YOUR-PASSWORD` in `DATABASE_URL`; URL-encode special characters in the password.

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 0.0.0.0:8000
```

Open <http://localhost:8000>. Demo accounts use password `DemoPass!246`:

- `director@demo.cambrify.local`
- `teacher@demo.cambrify.local`

Do not seed demonstration users in production.

### Local PostgreSQL with Docker

```bash
docker compose up --build
```

The application is available on <http://localhost:8000>.

## Supabase configuration

The non-secret project URL and publishable key supplied for this project are included in `.env.example`. The database password is never stored in Git.

1. Copy the connection string from Supabase **Project settings → Database**.
2. Prefer a pooler URL on IPv4-only or autoscaling hosting.
3. Include `sslmode=require`.
4. Run `python manage.py migrate` with a protected migration/owner connection.
5. Use a dedicated non-owner role for routine web traffic before enabling [`docs/supabase_rls.sql`](docs/supabase_rls.sql).

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for production instructions.

## Quality checks

```bash
ruff check .
ruff format --check .
python manage.py makemigrations --check --dry-run
coverage run manage.py test
coverage report
python manage.py check --deploy
```

## API

Foundation routes:

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/auth/login/` | Create a session |
| `POST` | `/api/auth/logout/` | End a session |
| `GET` | `/api/me/` | Current identity, tenant and role |
| `GET` | `/api/me/assignments/` | Current teacher's active, tenant-scoped assignments |
| `GET` | `/health/` | Database-backed liveness check |

See [`docs/API.md`](docs/API.md).

## Security and tenant isolation

Every protected request resolves one active `Membership`. School-owned models use a tenant manager that returns no rows when no tenant context exists. Teachers are additionally filtered by user and effective assignment dates. UI hiding is not treated as authorisation; direct unauthorised requests return 403/404.

Postgres transaction settings support RLS as a backstop. Application permissions remain mandatory. See [`docs/SECURITY.md`](docs/SECURITY.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Important template gate

Do not use the annotated flattened `TEMPLATE.pdf` as a production PDF background. Lesson Plan rendering requires a clean approved master or signed-off recreation. Work Plan field classifications also require template-owner confirmation before `TemplateVersion 1` is published.
