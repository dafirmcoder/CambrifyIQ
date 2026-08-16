# Deployment with Supabase PostgreSQL

## 1. Configure Supabase

The project URL is already represented in `.env.example`. In Supabase, copy a Postgres connection string from **Project settings → Database**. Prefer the transaction/session pooler string if the direct `db.*.supabase.co` hostname is unavailable from an IPv4-only host.

Set `DATABASE_URL` in the deployment secret manager. URL-encode special characters in the password. Do not place the password in Git or browser code.

For the first deployment, the owner connection can run migrations:

```bash
python manage.py migrate --noinput
```

For production, create a dedicated non-owner web role, grant only required table/sequence access, then test the policy baseline in `docs/supabase_rls.sql` on staging. Keep the database owner URL only in a protected migration job.

## 2. Required production environment

```dotenv
DJANGO_SECRET_KEY=<50+ random characters>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=app.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://app.example.com
DJANGO_SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
APP_URL=https://app.example.com
DATABASE_URL=<Supabase pooler URL with sslmode=require>
EMAIL_BACKEND=<transactional email backend>
DEFAULT_FROM_EMAIL=CambrifyIQ <noreply@example.com>
```

The provided Supabase publishable key is not used by the Django session foundation yet. Keep `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` configured for future protected storage/browser integration. Never substitute a Supabase service-role key in frontend code.

## 3. Build and release

```bash
docker build -t cambrifyiq .
docker run --rm --env-file .env cambrifyiq python manage.py check --deploy
docker run --rm --env-file .env cambrifyiq python manage.py migrate --noinput
docker run --env-file .env -p 8000:8000 cambrifyiq
```

The container listens on `0.0.0.0:8000` and serves collected static assets through WhiteNoise. Put it behind a TLS-terminating load balancer. Configure `/health/` as the liveness check.

## 4. Post-deploy checks

1. Create a school as a Head/Director.
2. Save school details and send an invitation.
3. Accept the invitation in a separate browser.
4. Confirm a Teacher gets 403 for `/school/team/`.
5. Confirm an invalid school switch returns 404.
6. Run the cross-school and RLS staging suites.
7. Verify managed backups and perform a restore rehearsal.

Template source PDFs and generated documents must use protected object storage; local media storage is not production-ready.
