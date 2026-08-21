# Antigravity prompt — Work Plan UI, school calendar & curriculum intake

Paste everything below the line into Google Antigravity. Attach your framework
files and the plan template when you send it.

---

## Context

You are working in **CambrifyIQ**, an existing multi-tenant Django 5.2 + DRF
application for Cambridge schools. Repo layout:

```
apps/accounts     custom email-first User
apps/schools      School, Membership, Invitation, AcademicYear, Term,
                  CalendarWeek, Subject, SchoolClass, TeacherAssignment, AuditLog
apps/curriculum   CurriculumFramework, SchemeOfWork, Topic, Subtopic,
                  LearningObjective, AssessmentObjective  (GLOBAL, not tenant-owned)
apps/planning     PlanningTemplate, TemplateVersion, TemplateField,
                  TemplateFieldOption, WorkPlan, WorkPlanWeek,
                  WorkPlanWeekObjective, WorkPlanEvent, LessonPlan*
apps/api          DRF: session auth, /api/me/, /api/work-plans/*
apps/core         TenantMiddleware, school_required/roles_required decorators
templates/        server-rendered Django templates, base.html app shell
static/css/app.css  single hand-written stylesheet (design tokens in :root)
```

Stack rules that are **non-negotiable**:

- Server-rendered Django templates. **No React, Vue, SPA, Tailwind, Bootstrap
  or any new frontend build step.** Progressive enhancement with vanilla JS
  only (`static/js/app.js` pattern, `fetch`, no bundler).
- Extend `static/css/app.css` using the existing tokens (`--ink`, `--blue`,
  `--teal`, `--line`, `--canvas`, `--radius`, `--shadow`) and the existing
  component classes (`.page-shell`, `.page-header`, `.panel`, `.panel__body`,
  `.form-grid`, `.field`, `.data-table`, `.button--primary`, `.role-chip`,
  `.empty-state`, `.status`). Match `templates/schools/team.html` and
  `templates/schools/settings.html` — those are the visual reference.
  Do not restyle existing pages.
- **Never** query a tenant-owned model with `all_objects` in a view.
  `objects` is the fail-closed `SchoolScopedManager`; it returns `.none()`
  without tenant context. `all_objects` is for services/tests only.
- Every state-changing view: `@login_required`, `@school_required`, an explicit
  role check, CSRF, POST-only for mutations. UI hiding is not authorisation —
  a direct unauthorised request must return 403/404.
- Write `AuditLog` entries for school-configuration changes, mirroring
  `apps/schools/views.py::school_settings`.
- Add tests in the existing `apps/*/tests.py` style (Django `TestCase`,
  including adversarial cross-tenant and wrong-role cases).
- Keep `ruff check .`, `ruff format --check .`,
  `python manage.py makemigrations --check --dry-run` and
  `python manage.py test` green.
- Read the model layer before writing code. It is already strict
  (`full_clean()` in `save()`, immutability guards, unique constraints); work
  with it, do not loosen it.

## Current state — read this before planning

- The **models and services are production-grade**. `apps/planning/services.py`
  already implements `create_work_plan`, `save_work_plan` (optimistic locking
  via `revision`), `transition_work_plan` (guarded state machine + immutable
  `WorkPlanEvent`), and the DRF API in `apps/api/` exposes create/save/submit/
  review/return/approve.
- The **UI is a placeholder**. `templates/planning/work_plan_list.html` is 18
  lines and `work_plan_detail.html` is 25 lines of unstyled fields.
- **A newly registered school cannot create a Work Plan at all**, because
  `create_work_plan()` requires:
  1. a published `TemplateVersion` of type `semester_work_plan` — no UI or seed
     creates one; and
  2. `term.calendar_weeks` to be non-empty — there is **no page anywhere** to
     create an `AcademicYear`, a `Term` or a `CalendarWeek`. Only `/admin/`.
  Fixing this is the first priority.

---

# Deliverables

Build the following in order. Ship each part as an independently working,
tested increment, and pause after each part for review.

## Part 1 — School academic setup & calendar creation (highest priority)

New section under `/school/`, reachable from the `School setup` nav item and
from the dashboard readiness checklist. Restrict to
`membership.can_manage_school` (Head/Director) plus Coordinator where noted.
Extend `templates/schools/settings.html`'s `.settings-nav` with the new tabs so
school administration is one coherent area:

**School profile · Academic years · Calendar · Subjects · Classes · Teaching
assignments · People & roles**

### 1a. Academic years
- List / create / edit `AcademicYear` (name, `starts_on`, `ends_on`,
  `is_current`).
- Setting `is_current` must atomically clear it on the school's other years.
  Add a migration enforcing one current year per school
  (`UniqueConstraint(fields=("school",), condition=Q(is_current=True))`).
- Validate no date overlap between years in the same school.
- Block delete when terms exist; offer archive instead.

### 1b. Terms / semesters
- Nested under a year: list / create / edit `Term` (name, `sequence`,
  `starts_on`, `ends_on`, `is_active`).
- Validate the term sits inside its academic year and does not overlap
  siblings. Surface these as field errors, not 500s.

### 1c. Calendar weeks — the centrepiece
This is what school admins will use every term, so make it good.

- A **week builder** for a term with a "Generate weeks" action: given the term
  dates and a chosen week start day (default Monday), create `CalendarWeek`
  rows with `sequence` 1..N, correct `starts_on`/`ends_on` (clamped to the term
  bounds) and an auto-filled `month_label` (e.g. `SEPTEMBER`, or
  `SEP/OCT` when a week straddles months).
- Generation is idempotent and **never touches weeks already snapshotted into
  a Work Plan**. Preview the result before committing.
- An editable week grid (`.data-table`): Sequence · Dates · Month label ·
  Type (Instructional / Special event) · Event label · Actions.
  Toggling to Special event reveals `event_label` and is what the Work Plan
  editor uses to lock a row.
- **Insert week above/below**, **delete week**, **move up/down** — all
  resequencing inside `transaction.atomic()` so the
  `unique_term_calendar_week` constraint never trips mid-update. Save the
  whole grid in one POST.
- Validate every week falls inside its term and that weeks do not overlap.
- **Protect in-use weeks.** `WorkPlanWeek.calendar_week` is `on_delete=PROTECT`
  — catch `ProtectedError` and show *"Week 9 is used by 3 Work Plans and
  cannot be deleted"*. Show an "in use by N plans" badge per row and disable
  destructive controls for those rows.
- **Drift warning.** `WorkPlanWeek` snapshots `week_label`, `month_label`,
  `event_label` and `is_instructional` at plan-creation time. When an admin
  edits a week that plans already snapshotted, warn explicitly and record an
  `AuditLog` entry. Do **not** silently mutate existing plans.
- **Clone calendar**: copy a term's week structure into another term, shifting
  dates.
- A read-only calendar view for teachers and Coordinators.

### 1d. Subjects, classes, teaching assignments
- CRUD for `Subject` (name, code, `cambridge_code`), `SchoolClass` (name,
  `year_group`, `boys_count`, `girls_count`), `TeacherAssignment` (teacher from
  active teacher memberships, subject, class, `effective_from`,
  `effective_until`).
- **Critical:** `Subject.cambridge_code` and `SchoolClass.year_group` are the
  only join to the global curriculum
  (`SchemeOfWork.subject_code` / `SchemeOfWork.year_group`). Today they are
  free text, so a typo silently makes Work Plan creation impossible.
  Render them as **dropdowns populated from distinct values on active
  `SchemeOfWork` rows**, with a clear "not mapped to a Cambridge scheme yet"
  warning state. Show a mapping-health indicator on the subject list.
- Bulk-add helper for classes and subjects, plus CSV paste-in if cheap.

### 1e. Readiness
Update `apps/dashboard/views.py` and `templates/dashboard/home.html` so the
readiness checklist reflects reality (academic year → term → calendar weeks →
subjects → classes → assignments → team) and each item deep-links to the page
that fixes it.

## Part 2 — Curriculum framework intake

I have framework files containing **lesson objectives per unit for every
subject** (attached). Ingest them into the existing global curriculum models.

- Inspect the attached files first and write the importer against their real
  shape. Do not invent a schema. If a file is ambiguous, ask me before coding.
- Build `python manage.py import_curriculum <path> [--framework CODE]
  [--dry-run]`:
  - Accepts CSV/XLSX (and JSON if that is what I supplied).
  - Creates/updates `CurriculumFramework` → `SchemeOfWork` (per subject ×
    year group, versioned) → `Topic`/unit → optional `Subtopic` →
    `LearningObjective` (and `AssessmentObjective` if present).
  - `--dry-run` prints a validation report (rows read, created, updated,
    skipped, duplicate LO codes, missing sequences, orphan objectives) and
    writes nothing.
  - Idempotent: re-running the same file changes nothing. Matches on
    (`framework`, `subject_code`, `year_group`, `version`) and LO `code`.
  - Never mutates a `SchemeOfWork` that is referenced by an approved Work
    Plan; publish a new `version` instead.
  - Wrapped in `transaction.atomic()`, with a clear per-row error report.
- Document the expected column format in `docs/CURRICULUM_IMPORT.md` with a
  sample file in `docs/samples/`.
- Add a read-only **Curriculum browser** at `/curriculum/` for Coordinators,
  Heads and Directors: framework → subject → year group → topics → LOs, with
  search and LO counts. Server-rendered, paginated, using `.data-table`.
- Also add a `seed_curriculum` fixture path so local dev and tests have real
  data.

## Part 3 — Adopt the Work Plan template

I am supplying the plan template we standardise on (attached).

- Read the attached template and derive the field map. Ask me before guessing
  any field's classification.
- Add `python manage.py provision_planning_templates [--school SLUG]` that
  creates a `PlanningTemplate` (type `semester_work_plan`) and publishes
  `TemplateVersion` v1 with its `TemplateField` rows
  (`field_class` red = controlled, blue = teacher-entered, system =
  generated; `control_type`; `sequence`; `is_required`).
- Call the same provisioning helper from `register_school()` so **every new
  school gets a usable published Work Plan template automatically** and the
  "No published Semester Work Plan template is available" wall disappears.
- Remember: published/retired `TemplateVersion`s and their fields are
  immutable by model guard. Revisions mean a **new version**, never an edit.
- Make `apps/planning/pdf.py` render from the template's field map and the
  school calendar (variable page count — the three-page sample is a reference,
  not a limit), and put the school name/logo, class, subject, academic year
  and term in the header the way the template does.

## Part 4 — Rebuild the Work Plan creation page (`/planning/work-plans/`)

Replace `templates/planning/work_plan_list.html` and the `work_plan_list` view.

### Access
- Teachers: see and create their own plans.
- Coordinator / Head / Director: **must not get a 403** (they do today).
  They get a school-wide, read-only list with filters and a link into the
  review queue.
- Add "Planning" to `base.html`'s nav for every role.

### Create flow
Replace the four flat dropdowns with a guided flow:

1. **Pick the class you are planning for** — cards, not a `<select>`:
   subject · class · year group, from the teacher's active assignments, each
   showing whether a plan already exists for the current term.
2. **Confirm year and term** — default to `AcademicYear.is_current` and the
   term containing today. Show week count and date range per term, and warn
   *"This term has no calendar weeks yet"* with a link to the calendar page
   (or an explanation to contact the school admin, for teachers).
3. **Scheme is derived, not chosen.** Resolve it from
   `assignment.subject.cambridge_code` + `school_class.year_group`. Display it
   read-only. Only ask the user when multiple active versions match; if none
   matches, show a specific, actionable error naming the missing mapping —
   never the current generic "Choose the scheme for this assignment's subject
   and year group."
4. Confirm → `create_work_plan()` → redirect to the editor.

Progressive enhancement: the flow must still work without JS (plain POSTs);
JS just makes it feel like one screen.

### Plan list
- `.data-table` or cards grouped by term: subject · class · term · status
  chip · **completion ("6 of 17 weeks planned")** · last updated · actions
  (Open, PDF, Archive).
- Status chips coloured per status (draft, submitted, under review, returned,
  resubmitted, approved, archived) — extend `.status` in `app.css`.
- Filters: term, status, and for leaders also teacher and subject. Pagination.
- Handle the `one_active_work_plan_per_assignment_term` constraint gracefully:
  *"You already have a Work Plan for Physics · Year 9 this term"* + a link,
  not a database constraint message.
- Real empty states with the correct next action for the user's role.

## Part 5 — Rebuild the Work Plan editor (`/planning/work-plans/<id>/`)

This is the most important screen in the product and the current one will not
survive real data: it renders **every LO in the scheme as a checkbox for every
week** (17 weeks × ~120 LOs ≈ 2,000 checkboxes in one DOM).

### Layout
- A **week grid mirroring the PDF**: Month · Week (sequence + dates) · Topic ·
  Learning objectives · Remarks. Sticky header, sticky first column,
  horizontally scrollable, readable on a tablet.
- Special-event weeks render as a visually distinct, locked row showing
  `event_label` — **but their Remarks field must stay editable.** Today the
  whole `<fieldset>` is `disabled`, so remarks are never submitted and
  `save_work_plan` wipes them. Fix this.
- Per-week status affordance: empty / topic only / complete.
- A summary rail: term, class, subject, scheme version, revision, status,
  weeks planned, LO coverage across the scheme, plus Resources.

### Learning-objective picker
- Selecting a **topic filters the LOs to that topic** (plus scheme-level LOs
  with no topic). Never render the full scheme per week.
- Open LOs in a **modal/drawer per week**, loaded on demand from a new
  JSON endpoint (`GET /api/schemes/<id>/objectives/?topic=<id>&q=`), scoped
  and permission-checked. Search by code and text. Show selected count on the
  closed row and the codes as removable chips.
- Warn (do not block) on an LO already used in another week of the same plan.

### Saving
- **Real autosave**: debounced per-row `fetch` POSTs against the existing
  `save_work_plan` service (extend the DRF endpoint if that is cleaner),
  sending the `revision`. Show "Saving… / Saved HH:MM / Save failed — retry".
- On a revision conflict, **never discard the user's input**: keep the local
  values, show a conflict banner, and offer Reload / Overwrite.
- Full-page POST must still work with JS disabled.
- Fix the N+1: bulk-create `WorkPlanWeekObjective` rows and precompute
  selected IDs in the view instead of the `stringformat` membership test
  inside a nested template loop.

### Workflow
- **Pre-submit validation.** `transition_work_plan` currently lets a teacher
  submit a completely empty plan — `transition_lesson_plan` validates, this
  does not. Require every instructional week to have a topic and at least one
  LO (or an explicit "intentionally blank" acknowledgement), and show a
  blocking checklist before the Submit button activates.
- **Show the workflow history.** `WorkPlanEvent` records every transition,
  actor, timestamp and comment, and nothing surfaces it. A returned plan must
  show the reviewer's comment prominently at the top so the teacher knows what
  to fix. Add a timeline panel.
- Submit / resubmit for the author; Start review / Return with comment /
  Approve for leaders, driven by role, with the return comment required.

## Part 6 — Leader review queue

`/planning/review/` for Coordinator, Head and Director. The service and API
already exist; this is the missing UI.

- Queue of submitted / under-review / resubmitted plans across the school:
  teacher · subject · class · term · submitted date · age.
- Filters (status, teacher, subject, term) and counts.
- Read-only plan view with the same week grid, plus Return (comment required)
  and Approve actions, and the event timeline.
- Approved plans link to the PDF.

---

## Definition of done

- A brand-new school can go: register → set up year, term, calendar, subjects,
  classes, assignments → teacher creates a Work Plan → fills 17 weeks with
  topics and LOs → submits → leader returns with a comment → teacher fixes and
  resubmits → leader approves → PDF downloads. **With zero use of
  `/admin/`.**
- Curriculum imports from my attached framework files, idempotently, with a
  dry-run report.
- Every new page matches the existing design language and is usable at 375px,
  768px and 1440px.
- Keyboard accessible, labelled controls, visible focus, `aria-live` for
  autosave status.
- Cross-tenant and wrong-role requests return 403/404, covered by tests.
- `ruff check .`, `ruff format --check .`, `makemigrations --check --dry-run`
  and `python manage.py test` all pass.
- `docs/ROADMAP.md` and `README.md` updated to reflect what actually shipped.

## Ask me before you assume

1. Field classification and exact labels for the plan template
   (which fields are controlled vs teacher-entered).
2. The column layout of my framework files, if it is not unambiguous.
3. Whether approval is Head-only or Head-then-Director.
4. Whether a Coordinator may edit the school calendar or only view it.
5. Whether editing a calendar week should offer to resync existing Work Plan
   snapshots, or always leave them frozen.

Start by reading `apps/planning/`, `apps/schools/models.py`,
`apps/curriculum/models.py` and `static/css/app.css`, then give me a short
implementation plan for **Part 1 only** and wait for my go-ahead.
