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

## Completed: Phase 0 template lockdown (structure)

The application-side structure of the template lock is in place:

- Immutable `PlanningTemplate`, `TemplateVersion`, `TemplateField` and
  `TemplateFieldOption` models with source checksums and approval metadata.
- The verified annotation register from plan section 8.2 encoded in
  `apps/planning/register.py`: four RED pickers, three BLUE text inputs and five
  fixed/system entries, with the measured PDF placement boxes.
- Locked annotation rules enforced at the model layer — a RED field cannot accept free
  text and must declare an authorised option source; a BLUE field cannot be a picker or
  read-only; `LP-D02` and `LP-D03` stay two IDs despite the touching circles.
- The `draft → in_review → approved → current → superseded` lifecycle, where approval
  freezes the definition and publishing supersedes the previous current version.
- `validate_for_lock` gates approval on the acceptance criteria in section 8.8.
- The Work Plan register recorded as a *proposal*, with the three-page landscape layout,
  weeks 1–17 and the fixed events for weeks 15–17.
- `manage.py bootstrap_templates <SCHOOL_CODE>` seeds draft versions and prints the
  outstanding blockers.

### Still blocked on the owner decisions in plan section 21.2

Code paths exist for each of these, but the values must be confirmed before
TemplateVersion 1 can be approved and published:

1. Clean, unmarked, high-resolution Lesson Plan master or written recreation approval.
   Until this is attached and approved, `is_renderable` stays false and approval is
   refused — `TEMPLATE.pdf` is a flattened raster whose annotation circles are part of
   the image and can never back production output.
2. Completed representative Lesson Plan and Work Plan samples for output comparison.
3. Work Plan confirmation for controlled Topic/LO, free-text Remarks/Resources and
   whether weeks 15–17 are editable.
4. Required/max/overflow rules for LP-T01, LP-T02 and LP-T03. Required flags and
   overflow policies are seeded from the register and need owner sign-off on the limits.
5. One-page Lesson Plan continuation policy.
6. Head-only versus optional Director-final approval. Both Head and Director can
   currently approve; narrowing is a one-line change to `APPROVER_ROLES`.

Side-by-side PDF prototypes follow once the clean master arrives.

## Completed: Phase 1 curriculum and access core

- Versioned `SchemeOfWork`, `Topic`, `Subtopic`, `LearningObjective` and
  `AssessmentObjective` models with stable codes, ordering and active windows.
- Historical labels preserved so approved plans stay reproducible; deactivated rows
  remain readable but leave the selectable option set.
- Scoped option services that intersect membership and active assignments before
  returning any value, plus server-side re-validation of submitted option IDs.
- Coordinator/Head content permissions per the section 6.2 matrix.
- Validated CSV imports with a quality report, duplicate and relationship checks,
  dry-run support and an all-or-nothing transaction:
  `manage.py import_curriculum <SCHOOL_CODE> <SCHEME_CODE> <file.csv> [--dry-run]`.
- Scoped `/api/schemes/`, `/api/schemes/{id}/objectives/`, `/api/templates/` and
  `/api/templates/{id}/fields/` endpoints.
- Adversarial cross-teacher and cross-school tests covering all of the above.

Remaining for this phase: the school setup UI for academic years, semesters, weeks,
subjects, classes and assignments, XLSX import alongside CSV, and the complete RLS
policy test suite.

## Phase 2: Semester Work Plans

- Calendar-generated weeks 1–17 and special events.
- Scoped Topic/LO controls, remarks and resources.
- Revision-token autosave, draft validation and three-page landscape renderer.
- Submit/review/return/resubmit/approve/archive workflow.

## Phase 3: Lesson Plans

- Exact LP-D01–LP-D04 controlled fields and LP-T01–LP-T03 text fields.
- Assignment/Work Plan carry-forward and roster-backed attendance.
- Clean A4 PDF overlay, overflow rules and visual regression.

## Later phases

Leadership reporting, protected object storage, notifications, conflict-aware offline
drafts, PWA hardening, Android TWA packaging, four-role UAT and production launch
hardening follow the approved plan.
