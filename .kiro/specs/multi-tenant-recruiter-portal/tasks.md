# Tasks — Multi-Tenant Recruiter Portal

> **Status:** Living tracker. Tick `[x]` as work merges. Each task cites the
> requirement(s) it satisfies (see `requirements.md`) and follows `design.md`.
>
> **Rule:** every phase is independently shippable — the app must run after each
> phase (R8.2). Do phases in order; within a phase, order is a suggestion.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## Phase 0 — Foundations (DB-backed auth, no behavior change yet)

Goal: stop using flat JSON for accounts; start using the ORM `user_account` /
`role` that already exist. No tenant features yet — just move the floor under us.

- [ ] 0.1 Add a `job_seeker` role to `db/seed.py` roles (currently `candidate`);
  decide final role names: `platform_admin`, `recruiter`, `job_seeker`. _(R2.1)_
- [ ] 0.2 Write `auth` DB-backed functions (register/authenticate/get/update) that
  read/write `user_account` instead of `data/*.json`, keeping the **same function
  signatures** so routes don't change. _(R2.1, R8.3)_
- [ ] 0.3 Carry bcrypt hashes unchanged; verify an existing candidate can still log
  in after cutover. _(R8.3)_
- [ ] 0.4 Migration script `scripts/migrate_json_to_db.py` (idempotent, dry-run
  flag, JSON kept as backup): candidates/admins → `user_account`,
  resumes meta → `candidate_profile`. _(R8.1)_
- [ ] 0.5 Tests: register/login against DB; migration maps N candidates with no lost
  logins. _(R8.1, R8.3)_

**Phase 0 done when:** app runs on DB-backed auth; existing logins work; migration
script proven on a copy of real JSON.

---

## Phase 1 — Company tenant + recruiter accounts

Goal: introduce the tenant and the recruiter identity.

- [ ] 1.1 ORM model `Company` (`company_id`, name, created_by, created_at) + table
  create via `init_db`. _(R1.1)_
- [ ] 1.2 Add `company_id` FK to `user_account` (nullable; set for recruiters). _(R2.4)_
- [ ] 1.3 Platform-admin route to **create a company**. _(R1.1)_
- [ ] 1.4 Recruiter **invitation** flow (create pending recruiter bound to a company;
  email send may be stubbed/logged initially). _(R2.2, R2.3)_
- [ ] 1.5 On login, load `role` and `company_id` into the session
  (extend `auth.set_session`). _(R2.4)_
- [ ] 1.6 Retire shared-key recruiter signup; keep `ADMIN_KEY` only to bootstrap the
  first platform admin. _(R2.3, R9.1)_
- [ ] 1.7 Tests: invited recruiter logs in and session carries the right
  `company_id`; shared-key cannot mint a recruiter. _(R2.2, R2.3)_

**Phase 1 done when:** a platform admin can create a company and invite a recruiter
who logs in with a company context.

---

## Phase 2 — Tenant isolation (the core)

Goal: hard, server-side data isolation. **Highest priority once Phase 1 lands.**

- [ ] 2.1 Add `company_id` + `posted_by` to `job_description`; move JDs to a DB table
  (or add columns if still file-backed in a transitional step). _(R4.1)_
- [ ] 2.2 Backfill `company_id` on existing JDs from distinct `company` strings →
  `Company` rows; unmatched → "Legacy / Unassigned". _(R1.3, R8.1)_
- [ ] 2.3 Guard decorators: `@role_required(...)` and a `scoped_query(model)` /
  `get_for_company(model, id, company_id)` helper. _(R3.1, R3.3)_
- [ ] 2.4 Apply guards to all recruiter JD routes (list/view/edit/deactivate/
  reactivate) — reads and writes scoped to session company. _(R4.2, R3.1)_
- [ ] 2.5 Cross-tenant access → 404/403 **and** audit event `cross_tenant_denied`.
  _(R3.2, R9.3)_
- [ ] 2.6 Platform-admin unscoped path preserved. _(R3.4)_
- [ ] 2.7 Keep JD embedding on create/edit/reactivate (no regression). _(R4.3)_
- [ ] 2.8 **Isolation test suite:** two companies, two recruiters; recruiter A gets
  403/404 for every one of B's resources (parametrized over all recruiter routes).
  _(R3.1, R3.2)_

**Phase 2 done when:** the isolation test suite is green for every recruiter route.

---

## Phase 3 — Functional Apply + application tracking

Goal: make the existing Apply button real and tracked.

- [ ] 3.1 ORM model `Application` (`application_id`, `jd_id`, `candidate_id`,
  `company_id` denormalized, `status`, `applied_at`, `viewed_by`,
  `status_changed_at`) + unique constraint `(jd_id, candidate_id)`. _(R5.1, R5.2)_
- [ ] 3.2 Wire the Apply button → POST endpoint: checks resume exists, JD active, not
  already applied; inserts `application` with `company_id` copied from the JD.
  _(R5.1, R5.2, R5.3, R5.4)_
- [ ] 3.3 Job-seeker **Application Tracker** page (their applications + status).
  _(R7.1)_
- [ ] 3.4 Tests: apply happy path; duplicate = no-op; no-resume blocked; inactive JD
  rejected. _(R5.1–R5.4)_

**Phase 3 done when:** a job seeker applies, sees it in their tracker, and the row is
scoped to the JD's company.

---

## Phase 4 — Recruiter applicant management

Goal: recruiters work their pipeline; seekers see interest.

- [ ] 4.1 Recruiter **Applicants per JD** list: applicant + match score + status.
  _(R6.1)_
- [ ] 4.2 Opening an applicant sets `applied → viewed`, records `viewed_by` + time.
  _(R6.2)_
- [ ] 4.3 Status actions: `shortlisted` / `rejected` / `hired`, company-scoped, each
  writes `status_changed_at` + audit `application_status_changed`. _(R6.3, R9.3)_
- [ ] 4.4 Show **Suggested candidates** (existing `two_stage_match` top-K) alongside
  applicants, labeled "AI suggested". _(R6.4)_
- [ ] 4.5 Job-seeker tracker reflects "viewed"/"shortlisted" by recruiter. _(R7.2)_
- [ ] 4.6 Never expose other applicants to a job seeker. _(R7.3)_
- [ ] 4.7 Resume access gate: recruiter can open a resume only if that candidate
  applied to one of their company's JDs. _(R9.4)_
- [ ] 4.8 Tests: status transitions, viewed-by reflection, resume gate denies
  non-applicants. _(R6.2–R6.4, R7.2, R9.4)_

**Phase 4 done when:** recruiter can run the full Applied→Hired pipeline and the
seeker sees recruiter interest, all tenant-safe.

---

## Phase 5 — Route split, UI, and hardening

Goal: clean separation and production polish.

- [ ] 5.1 Split blueprints: `routes/candidate.py` → `jobseeker`; `routes/admin.py` →
  `recruiter` (company/JD/applicants) + `platform_admin` (companies/users/models).
  _(design §7)_
- [ ] 5.2 Recruiter dashboard: active JDs, total applicants, new applicants count.
  _(R6.1, product parity)_
- [ ] 5.3 Platform-admin console: companies, users, models, request/LLM metrics.
  _(R3.4)_
- [ ] 5.4 Audit events wired end-to-end: `cross_tenant_denied`,
  `application_created`, `application_status_changed`, `recruiter_viewed_candidate`.
  _(R9.3)_
- [ ] 5.5 Confirm no PII in logs across all new routes. _(R9.2)_
- [ ] 5.6 Remove flat-JSON read/write paths once DB cutover is verified (keep a final
  JSON backup/export). _(R8.1)_

**Phase 5 done when:** three real roles, scoped data, clean blueprints, full audit,
no JSON account store in the live path.

---

## Cross-cutting checklist (keep ticking throughout)
- [ ] Every new recruiter query goes through `scoped_query` / `get_for_company`
  (no raw cross-tenant query anywhere). _(R3.3)_
- [ ] Each phase keeps the app runnable and has its own tests. _(R8.2)_
- [ ] `requirements.md` success criteria (§5) all demonstrably met at the end.

---

## Suggested order & dependencies
```
Phase 0 ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──▶ Phase 5
(DB auth)   (company+   (ISOLATION  (apply)     (applicant  (split+
            recruiter)   — gate)                 pipeline)   polish)
```
Phase 2 (isolation) is the riskiest and most important — do not start Phase 3+
candidate-facing features before isolation tests are green.

---

## Progress log (append as you go)
| Date | Phase/Task | PR | Notes |
|---|---|---|---|
| _(add rows as work merges)_ | | | |
