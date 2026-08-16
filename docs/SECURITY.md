# Security model

## Implemented in the foundation

- Email-first Django authentication with Django's adaptive password hashing and validators.
- Secure session defaults, CSRF protection, clickjacking protection and MIME sniffing protection.
- Production switches for HTTPS redirect, secure cookies and HSTS.
- Four school roles with server-side policy checks.
- Active membership and school resolution on every authenticated request.
- Fail-closed tenant managers for school-owned records.
- Teacher assignment filters enforced server-side.
- Random invitation tokens stored only as SHA-256 hashes, expiring after seven days.
- Head accounts cannot appoint or modify Director accounts.
- Immutable application audit records for school creation, settings and membership events.
- API login throttling and uniform authentication errors.
- Health endpoint that reports no database details.
- Documented lint, test, migration drift and Django deployment checks.

## Supabase and RLS

`docs/supabase_rls.sql` is a defence-in-depth policy baseline for tenant-owned foundation tables. Apply it only after creating a dedicated non-owner application role and testing onboarding, invitation acceptance and administrative jobs in staging. The Supabase `postgres` owner can bypass RLS and should be reserved for migrations, not routine web traffic.

Django policy checks remain mandatory even when RLS is enabled. RLS does not replace assignment filtering, workflow permissions or object-level service validation.

## Secrets

Never commit `.env`, database passwords, service-role keys or SMTP credentials. The Supabase publishable key may be exposed to browser code, but is still configured through the environment. Rotate any credential accidentally committed or shared outside an approved secret manager.

## Before production

- Set a random 50+ character `DJANGO_SECRET_KEY`.
- Set `DEBUG=False`, exact allowed hosts/origins, HTTPS redirect, secure cookies and HSTS.
- Use a dedicated least-privileged Postgres role through the Supabase pooler.
- Configure transactional email, error monitoring and request-level rate limiting at the edge.
- Configure protected object storage before accepting template or generated PDF files.
- Run backup/restore, cross-school adversarial and RLS policy tests in staging.
- Add MFA before storing sensitive roster details.

Report security issues privately to the repository owner; do not open a public issue containing an exploit or school data.
