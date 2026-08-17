# Delivery roadmap mapped to the approved proposal

## Completed: Foundation milestone

- Django/DRF project, environment configuration and local quality checks.
- Supabase PostgreSQL connection support and RLS context baseline.
- Email-first authentication and Head/Director self-service school registration.
- Multi-school memberships with one validated active tenant per request.
- Teacher, Coordinator, Head and Director roles.
- Secure staff invitation, acceptance, role updates and suspension.
- School profile, academic-year/term, subject, class and assignment foundation models.
- Fail-closed tenant managers, assignment-scoped API and adversarial access tests.
- Role-aware dashboards, onboarding readiness and PWA shell/offline safety page.
- Immutable audit events, health endpoint, Docker and deployment guidance.

## Completed: Curriculum and template-definition foundation

- Global, system-administered curriculum frameworks, versioned schemes, topics,
  subtopics, learning objectives and assessment objectives.
- School-scoped planning templates, versioned source assets/checksums, PDF field
  definitions and bounded field options.
- Published/retired template versions and their definitions are immutable.

## In progress: Phase 2 — Semester Work Plans

- School-defined `CalendarWeek` records drive teaching weeks and special events.
- Assignment-scoped Work Plan drafts snapshot those weeks and permit curriculum-only
  Topic/LO selections plus free-text Remarks and Resources.
- Revision tokens, review/return/approval transitions and audit events are implemented.

The Work Plan renderer will paginate dynamically from the school calendar; the
three-page sample is a reference, not a page-count limit. Owner-approved visual
comparison remains pending the final field map.

## Next: Template lockdown and curriculum operations

This is blocked on the proposal's owner decisions and assets:

1. Clean, unmarked, high-resolution Lesson Plan master or written recreation approval.
2. Completed representative Lesson Plan and Work Plan samples.
3. Work Plan confirmation for controlled Topic/LO, free-text Remarks/Resources and fixed weeks 15–17.
4. Required/max/overflow rules for LP-T01, LP-T02 and LP-T03.
5. One-page Lesson Plan continuation policy.
6. Head-only versus optional Director-final approval.

The immutable `PlanningTemplate`, `TemplateVersion`, `TemplateField` and
`TemplateFieldOption` models are in place. Once supplied, register approved source
assets/checksums and build side-by-side PDF prototypes.

## Phase 1: Curriculum and access core

- School setup UI for academic years, semesters, weeks, subjects, classes and assignments.
- System-administered global, versioned schemes, topics, subtopics, LOs and AOs.
- Validated CSV/XLSX curriculum imports and quality reports.
- Coordinator content permissions and complete RLS policy tests.

## Phase 2: Semester Work Plans

- Calendar-generated weeks 1–17 and special events.
- Scoped Topic/LO controls, remarks and resources.
- Revision-token autosave, draft validation and dynamically paginated landscape renderer.
- Submit/review/return/resubmit/approve/archive workflow.

## Phase 3: Lesson Plans

- Exact LP-D01–LP-D04 controlled fields and LP-T01–LP-T03 text fields.
- Assignment/Work Plan carry-forward and roster-backed attendance.
- Clean A4 PDF overlay, overflow rules and visual regression.

## Later phases

Leadership reporting, protected object storage, notifications, conflict-aware offline drafts, PWA hardening, Android TWA packaging, four-role UAT and production launch hardening follow the approved plan.
