# Work Plan creation — assessment (21 Aug 2026)

Scope reviewed: `apps/planning/{views,forms,services,models,pdf}.py`,
`templates/planning/*`, `apps/schools/{models,views,forms,urls}.py`,
`templates/schools/*`, `static/css/app.css`, `apps/curriculum/models.py`.

## 1. Verdict

The **data model is strong and the UI is a placeholder**. Every constraint the
product needs (tenant scoping, immutable template versions, revision tokens,
LO snapshots, guarded workflow, audit events) already exists in the models and
services layer. What is missing is everything a real user touches:

| Layer | State |
|---|---|
| Models / constraints | Production-grade |
| Services (create/save/transition) | Production-grade |
| REST API | Work Plan CRUD + workflow exists |
| **HTML UI** | **Two throwaway templates, 43 lines total** |
| **School calendar UI** | **Does not exist** |
| **Academic year / term / subject / class / assignment UI** | **Does not exist** |
| **Curriculum import** | **Does not exist** |
| **Planning template admin** | **Does not exist** |
| **Leader review UI** | **Does not exist** |

## 2. The blocking issue: a new school cannot create a Work Plan at all

`create_work_plan()` in `apps/planning/services.py` hard-fails on two
preconditions, and **neither has any UI anywhere in the app**:

1. `TemplateVersion` with `status=published` and
   `template_type=semester_work_plan` → *"No published Semester Work Plan
   template is available."*
   Only creatable through `/admin/`. There is no template builder, no
   importer, no seed command.
2. `term.calendar_weeks` must be non-empty → *"Add school calendar weeks to
   this term before creating a Work Plan."*
   `CalendarWeek` is registered with a bare `admin.site.register([...])` in
   `apps/schools/admin.py`. A school admin has **no page** to create an
   academic year, a term, or a single week.

The dashboard readiness checklist even says *"Add academic year"* and links to
`schools:settings`, which only edits name/phone/logo. `apps/schools/urls.py`
has six routes: settings, team, invite, update member, switch, accept
invitation. That is the entire school-admin surface.

So the current happy path for a Work Plan is: Django admin → academic year →
term → 17 calendar weeks by hand → planning template → template version →
publish → *then* the teacher page works. This is the single highest-priority
gap, and it matches what you flagged about calendar creation.

## 3. Work Plan creation page (`/planning/work-plans/`)

`templates/planning/work_plan_list.html` is 18 lines. Issues, ordered by
severity:

### Functional
1. **Leaders are locked out of the whole page.** `work_plan_list` raises
   `PermissionDenied` unless `role == TEACHER`. Coordinators, Heads and
   Directors get a 403 on the list, not just on the create form. There is no
   leader view of the school's plans anywhere in HTML.
2. **No dependent filtering.** Four independent dropdowns:
   - `academic_year` → all years
   - `term` → all active terms, *not* filtered by the chosen year
   - `scheme` → **every active `SchemeOfWork` globally**, i.e. every subject ×
     every year group × every version. Once you import the real frameworks
     this becomes an unusable list of hundreds of options.
   - `assignment` → correct (teacher + active)
   `clean()` already knows the rule (`scheme.subject_code ==
   assignment.subject.cambridge_code`, `scheme.year_group ==
   class.year_group`) — but only *after* submit, as a red error.
3. **Scheme should not be a user choice.** It is fully derivable from the
   assignment. At most show it read-only with a version selector when more
   than one active version matches.
4. **Year/term should default.** `AcademicYear.is_current` exists and the term
   containing today is computable. Neither is used.
5. **Duplicate plan → ugly error.** The
   `one_active_work_plan_per_assignment_term` constraint surfaces as
   *"Constraint "one_active_work_plan_per_assignment_term" is violated."* It
   should say "You already have a plan for this class and term" with a link to
   it.
6. **No nav entry.** `base.html` has Overview / People / School setup only.
   Teachers reach planning solely via a dashboard button; leaders never.

### Visual
7. Rendered as `{% for field in form %}<p><label>{{ field }}</label></p>`.
   The stylesheet has `.form-grid`, `.field`, `.form-panel`, `.form-stack`,
   `.data-table` — none are used here. It is visually inconsistent with
   `schools/settings.html` and `schools/team.html`, which do use them.
8. "My plans" is a flat `<ul>` reusing `.assignment-list`. The status is
   crammed into `.assignment-code`, a 42×42px square built for a 5-character
   subject code — "Under review" and "Resubmitted" overflow it.
9. No filters, no term grouping, no counts, no progress ("6 of 17 weeks
   planned"), no PDF link, no archive/delete, no pagination.
10. Empty state says "Complete the school calendar and create your first
    draft above" — with no link to a calendar page, because there isn't one.

## 4. Work Plan detail / editor — the real UI problem

`templates/planning/work_plan_detail.html` (25 lines) renders, **for every
week, every learning objective in the entire scheme** as a checkbox:

```
{% for week in weeks %} ... {% for objective in objectives %}<input type="checkbox">
```

With a realistic Cambridge framework (say 120 LOs) and a 17-week term that is
**2,040 checkboxes and 17 full topic selects in one DOM**, with no filtering by
the week's selected topic, no search, no virtualisation, no collapse. This page
will be unusable the moment you import your frameworks.

Other defects:

11. **Remarks are wiped on special-event weeks.** The whole `<fieldset>` is
    `disabled` when `not week.is_instructional`, so the remarks textarea is
    never submitted; `save_work_plan` then writes `""` over any existing
    remark. The model explicitly permits remarks on event weeks.
12. **No autosave**, despite the README claiming "revision-safe autosave". It
    is a single full-page POST. On a revision conflict the user gets
    *"This plan changed elsewhere. Refresh before saving."* and **loses every
    keystroke on the page**.
13. **Work Plans can be submitted empty.** `transition_lesson_plan` checks
    required fields before submit; `transition_work_plan` does not. A teacher
    can submit a plan with zero topics.
14. **Workflow history is never shown.** `WorkPlanEvent` records every
    return + comment. A returned plan renders no reason, so the teacher
    cannot see what to fix.
15. No week-grid table matching the landscape PDF (Month / Week / Topic + LOs
    / Remarks). The PDF renderer in `apps/planning/pdf.py` already uses that
    shape; the screen does not mirror it.
16. `objective_selections` are deleted and re-created row-by-row on every
    save (N+1 inserts), and `objective.pk|stringformat:"s" in
    week.selected_objective_ids` runs weeks × LOs times per render.
17. No mobile treatment; nested `<fieldset>` stacks are unreadable under
    760px.

## 5. Calendar creation (what school admins need)

`CalendarWeek` already carries `term`, `sequence`, `starts_on`, `ends_on`,
`month_label`, `event_label`, `is_instructional` — the model is right. Missing:

- Academic-year CRUD (and `is_current` is not enforced unique per school —
  two "current" years are possible today).
- Term CRUD; no validation that terms sit inside the year or don't overlap.
- **Week auto-generation**: pick term start/end → generate Mon–Fri weeks,
  auto-fill `month_label`, then let the admin mark breaks/exams as
  non-instructional with an `event_label`. No validation exists that a week
  falls within its term.
- Insert / reorder / delete a week (`sequence` is unique per term, so
  inserting means resequencing in a transaction).
- **Deletion guard**: `WorkPlanWeek.calendar_week` is `on_delete=PROTECT` — a
  week already snapshotted into a plan will raise `ProtectedError` (500).
- **Drift policy**: `WorkPlanWeek` snapshots labels at creation. If an admin
  edits week 9 after plans exist, plans keep stale labels with no warning. You
  need either a lock ("calendar in use") or an explicit resync action.
- Term/year cloning for the next academic year.

## 6. Curriculum frameworks and the plan template

For the LO-per-unit frameworks you are about to feed in:

- There is **no importer**. `SchemeOfWork`, `Topic`, `Subtopic`,
  `LearningObjective`, `AssessmentObjective` are admin-only. `docs/ROADMAP.md`
  lists "Validated CSV/XLSX curriculum imports" as unbuilt Phase 1 work.
- Curriculum is **global, not per-school** (by design). Whoever imports needs
  superuser / a management command, not a school role.
- The link from a school subject to a scheme is
  `Subject.cambridge_code == SchemeOfWork.subject_code` and
  `SchoolClass.year_group == SchemeOfWork.year_group`. **Both are optional
  free-text fields today**, so a typo silently breaks Work Plan creation.
  The subject/class admin UI must make these pickers, not text inputs.
- For the template you want to adopt: `TemplateVersion` + `TemplateField`
  support a full field map with PDF coordinates, and lock on publish. Nothing
  populates them. The pragmatic path is a seed/import command that publishes
  v1 of a Semester Work Plan template per school at onboarding, so the
  "no published template" wall disappears.

## 7. Suggested build order

1. **School admin: academic structure + calendar** (unblocks everything).
2. **Curriculum import command + framework adoption** (feeds real LOs).
3. **Auto-provisioned planning template v1** (removes the second wall).
4. **Rebuild the Work Plan create page** (wizard, derived scheme, defaults).
5. **Rebuild the Work Plan editor** (week grid, topic-scoped LO picker,
   autosave, history).
6. **Leader review queue** (the API already exists).

`docs/prompts/ANTIGRAVITY_WORKPLAN_PROMPT.md` contains a ready-to-paste brief
covering 1–6.
