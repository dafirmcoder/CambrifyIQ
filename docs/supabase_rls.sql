-- CambrifyIQ tenant-data RLS baseline for Supabase PostgreSQL.
-- Review and test in staging before production. Run as a database owner.
-- The routine web connection should use a dedicated, non-owner role (for example cams_app).

begin;

create schema if not exists cams;

create or replace function cams.current_school_id()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('cams.school_id', true), '')::uuid
$$;

create or replace function cams.current_user_id()
returns bigint
language sql
stable
as $$
  select nullif(current_setting('cams.user_id', true), '')::bigint
$$;

-- Every table below has a direct school_id column. Django sets cams.school_id with
-- transaction-local set_config calls after validating an active membership.
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'schools_academicyear',
    'schools_term',
    'schools_subject',
    'schools_schoolclass',
    'schools_invitation',
    'schools_auditlog'
  ]
  loop
    execute format('alter table public.%I enable row level security', table_name);
    execute format('alter table public.%I force row level security', table_name);
    execute format('drop policy if exists cams_school_isolation on public.%I', table_name);
    execute format(
      'create policy cams_school_isolation on public.%I using (school_id = cams.current_school_id()) with check (school_id = cams.current_school_id())',
      table_name
    );
  end loop;
end $$;

alter table public.schools_teacherassignment enable row level security;
alter table public.schools_teacherassignment force row level security;
drop policy if exists cams_assignment_isolation on public.schools_teacherassignment;
create policy cams_assignment_isolation on public.schools_teacherassignment
  using (
    school_id = cams.current_school_id()
    and (
      teacher_id = cams.current_user_id()
      or exists (
        select 1 from public.schools_membership m
        where m.school_id = schools_teacherassignment.school_id
          and m.user_id = cams.current_user_id()
          and m.status = 'active'
          and m.role in ('coordinator', 'head', 'director')
      )
    )
  )
  with check (
    school_id = cams.current_school_id()
    and exists (
      select 1 from public.schools_membership m
      where m.school_id = schools_teacherassignment.school_id
        and m.user_id = cams.current_user_id()
        and m.status = 'active'
        and m.role in ('coordinator', 'head', 'director')
    )
  );

-- Grant the dedicated role only after creating it and setting its password via your
-- secret manager. Do not grant these privileges to anon/authenticated browser roles.
-- grant usage on schema public, cams to cams_app;
-- grant select, insert, update, delete on the listed tables to cams_app;
-- grant select on public.schools_membership to cams_app;

commit;
