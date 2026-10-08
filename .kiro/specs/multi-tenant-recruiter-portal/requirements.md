# Requirements — Multi-Tenant Recruiter Portal

> **Status:** Draft · living tracker (update as development progresses)
> **Product:** TalentLink — AI resume ↔ JD matching portal
> **Goal of this spec:** evolve the current single-admin demo into a Naukri-style
> multi-tenant platform where each recruiter logs in and sees only their own
> company's jobs, applicants, and candidate matches.

---

## 1. Where we are today (current flow)

This is the honest current state, read from the code — not the target.

### Actors today
- **Admin** — a single shared role. Anyone who knows the `ADMIN_KEY` (`config.py`,
  default `"admin123"`) can register as an admin. All admins see **all** JDs and
  **all** candidates. There is no company boundary.
- **Candidate (job seeker)** — registers, uploads a resume, sees AI-matched jobs,
  can bookmark ("save") jobs.

### How data is stored today
- **Accounts:** flat JSON files — `data/candidates.json`, `data/admins.json`
  (see `auth.py`). Passwords are bcrypt-hashed.
- **JDs:** a single flat file `data/jds.json` (see `routes/admin.py`). Each JD has
  `uploaded_by` (an admin id) but nothing scopes *reads* to that admin.
- **Parsed resumes:** `data/parsed_resumes/<candidate_id>.json`.
- **Vectors:** ChromaDB (`vector_matcher.py`).
- **Operational DB (Postgres/SQLite):** already present via SQLAlchemy
  (`db/models_orm.py`) but currently only stores `model_registry`, `llm_call`,
  `request_trace`, and **unused** `role` / `user_account` tables. Auth does **not**
  use these yet.

### Current journeys
```
Candidate:  register → login → upload resume (→ LLM parse → embed) → match-jobs
            → save jobs → (Apply button exists but is NOT functional)

Admin:      register with ADMIN_KEY → login → upload/edit/deactivate JDs
            → rank candidates for a JD (two-stage funnel) → view all candidates
            → manage model registry
```

### Key gaps that block "production / Naukri-style"
1. **No tenancy.** Every admin sees every JD and every candidate. A recruiter from
   Company A can see Company B's data.
2. **No `recruiter` role in the live app.** The DB seed already defines a
   `recruiter` role (`db/seed.py`) and the ORM has `Role`/`UserAccount`, but
   `auth.py` ignores them and only knows `admin` / `candidate`.
3. **No `Company` entity.** JDs carry a free-text `company` string, not a real
   organization that groups recruiters.
4. **Apply is cosmetic.** The candidate UI has an Apply button with no backend —
   nothing is persisted, recruiters can't see applicants.
5. **Shared secret auth.** One `ADMIN_KEY` for all privileged users; no invite,
   no per-company onboarding.

---

## 2. Where we are going (future flow)

A multi-tenant platform modeled on how Naukri / LinkedIn Recruiter behave.

### Target actors
| Role | Who | Sees / does |
|---|---|---|
| **Platform Admin** | Us (operators) | Manage companies, users, models, platform-wide health. Can impersonate for support. |
| **Company** | An organization (e.g. "Acme Corp") | Not a login — a tenant that *owns* JDs, recruiters, and applicant data. |
| **Recruiter** | A company user | Belongs to exactly one company. Posts/manages that company's JDs. Sees applicants + AI-suggested candidates **for their company only**. |
| **Job Seeker** | A candidate | One profile + resume. Browses all active jobs across companies, applies, tracks application status. |

### Target journeys
```
Platform Admin: login → create Company → invite first Recruiter (email invite)
                → oversee all tenants, models, metrics

Recruiter:      accept invite → login → post JD (under their Company)
                → view "Applicants" (who applied to their JDs)
                → view "Suggested" (AI top-K matches for their JDs)
                → shortlist / reject / change applicant status
                → see only their Company's data (hard isolation)

Job Seeker:     register → upload resume → browse/search jobs
                → Apply to a job  ← now functional
                → track application status (applied / viewed / shortlisted / rejected)
                → see who viewed/shortlisted them
```

### Naukri-style behaviors we are adopting (from general product knowledge)
- Recruiter dashboard summarizing **active JDs, total applicants, new applicants**.
- Per-JD applicant list with **status pipeline** (Applied → Viewed → Shortlisted →
  Rejected / Hired).
- **Suggested candidates** (our AI two-stage match) shown alongside real applicants.
- Candidate-side **application tracker** and **"recruiter viewed your profile"**.
- Strict **tenant isolation**: a recruiter can never load another company's JD,
  applicant, or candidate contact detail.

---

## 3. Requirements (EARS format)

Each requirement has a user story and acceptance criteria written in EARS
("WHEN ... THE SYSTEM SHALL ..."). IDs are stable so tasks/design can reference them.

### R1 — Company (tenant) entity
**Story:** As a platform admin, I want companies as first-class tenants, so that
jobs and recruiters belong to an organization.

- R1.1 WHEN a platform admin creates a company THE SYSTEM SHALL store a unique
  `company_id`, name, and created-at, and persist it in the operational DB.
- R1.2 THE SYSTEM SHALL associate every JD and every recruiter with exactly one
  `company_id`.
- R1.3 WHERE a legacy JD has only a free-text `company` string, THE SYSTEM SHALL
  map it to a company during migration (or a "Legacy / Unassigned" company).

### R2 — Recruiter role & accounts
**Story:** As a company, I want recruiter accounts tied to my company, so that my
team manages our hiring without a shared secret.

- R2.1 THE SYSTEM SHALL support three login roles: `platform_admin`, `recruiter`,
  `job_seeker`, backed by the DB `role` / `user_account` tables (not flat JSON).
- R2.2 WHEN a platform admin or an existing recruiter invites a user by email
  THE SYSTEM SHALL create a pending recruiter bound to that company.
- R2.3 THE SYSTEM SHALL NOT allow self-registration as a recruiter using a shared
  key; recruiter accounts are created only via invitation.
- R2.4 WHEN a recruiter authenticates THE SYSTEM SHALL load their `company_id` into
  the session.

### R3 — Tenant data isolation (the core requirement)
**Story:** As a recruiter, I must only ever see my own company's data.

- R3.1 WHEN a recruiter requests any JD, applicant list, or candidate detail
  THE SYSTEM SHALL return data only where the resource's `company_id` equals the
  recruiter's `company_id`.
- R3.2 IF a recruiter requests a resource belonging to another company THE SYSTEM
  SHALL respond 403 (or 404) and SHALL log the attempt to the audit trail.
- R3.3 THE SYSTEM SHALL enforce isolation on the server side (not in templates),
  on every recruiter route.
- R3.4 THE SYSTEM SHALL allow a platform admin to view across all companies.

### R4 — Job posting under a company
**Story:** As a recruiter, I post jobs that belong to my company.

- R4.1 WHEN a recruiter posts a JD THE SYSTEM SHALL set `company_id` and
  `posted_by` from the session, ignoring any client-supplied company value.
- R4.2 THE SYSTEM SHALL list, edit, deactivate, and reactivate only JDs within the
  recruiter's company.
- R4.3 THE SYSTEM SHALL continue to embed JD text in ChromaDB on create/edit/
  reactivate (existing behavior preserved).

### R5 — Functional Apply (job seeker → application)
**Story:** As a job seeker, when I click Apply I want my application recorded and
visible to the right recruiter.

- R5.1 WHEN a job seeker clicks Apply on an active JD THE SYSTEM SHALL create an
  `Application` linking `candidate_id` + `jd_id` + `company_id` with status
  `applied` and a timestamp.
- R5.2 THE SYSTEM SHALL prevent duplicate applications (one application per
  candidate per JD); a second Apply SHALL be a no-op/"already applied".
- R5.3 IF a job seeker has no uploaded resume THE SYSTEM SHALL block Apply and
  prompt them to upload a resume first.
- R5.4 WHEN a JD is inactive THE SYSTEM SHALL NOT accept new applications to it.

### R6 — Recruiter applicant management
**Story:** As a recruiter, I review and progress applicants for my JDs.

- R6.1 THE SYSTEM SHALL show, per JD, the list of applicants with their match score
  and current status.
- R6.2 WHEN a recruiter opens an applicant THE SYSTEM SHALL set status to `viewed`
  (if currently `applied`) and record who viewed and when.
- R6.3 THE SYSTEM SHALL let a recruiter set status to `shortlisted`, `rejected`, or
  `hired`, scoped to their company.
- R6.4 THE SYSTEM SHALL show AI "suggested candidates" (two-stage match top-K)
  alongside real applicants, clearly labeled as suggestions.

### R7 — Job seeker application tracking
**Story:** As a job seeker, I track my applications and recruiter interest.

- R7.1 THE SYSTEM SHALL show a job seeker all their applications with current status.
- R7.2 WHEN a recruiter views or shortlists a job seeker THE SYSTEM SHALL reflect
  that on the job seeker's tracker ("viewed by recruiter", "shortlisted").
- R7.3 THE SYSTEM SHALL NOT expose other applicants' identities to a job seeker.

### R8 — Migration & backward compatibility
**Story:** As the team, I want to move off flat JSON without losing current data.

- R8.1 THE SYSTEM SHALL provide a one-time migration moving candidates/admins/JDs
  from JSON files into the DB tables.
- R8.2 THE SYSTEM SHALL keep the app runnable at each phase (no big-bang rewrite);
  phases are shippable increments.
- R8.3 THE SYSTEM SHALL preserve existing candidate logins (bcrypt hashes carried
  over).

### R9 — Security & auditing (production hardening)
**Story:** As an operator, I need the multi-tenant app to be safe to run for real.

- R9.1 THE SYSTEM SHALL retire the shared `ADMIN_KEY` path for privileged signup
  once invitations exist.
- R9.2 THE SYSTEM SHALL never expose candidate PII (email, resume) across tenants
  or in logs (extends existing audit/trace behavior).
- R9.3 THE SYSTEM SHALL audit every cross-tenant access attempt and every applicant
  status change (actor, target, company, timestamp).
- R9.4 THE SYSTEM SHALL scope resume files in S3 per candidate (existing) and gate
  recruiter access to a resume behind "candidate applied to this company's JD".

---

## 4. Out of scope (for this spec)
- Payments / subscription plans for companies.
- Messaging/chat between recruiter and candidate.
- Email/SMS delivery infrastructure (invitations may stub the email send initially).
- Full-text job search ranking beyond current filters + AI matching.

---

## 5. Success criteria (definition of done for the whole spec)
- Two recruiters from two different companies log in and provably cannot see each
  other's JDs, applicants, or candidate contact details (R3 verified by tests).
- A job seeker's Apply click creates a tracked application visible to the correct
  recruiter and on the seeker's own tracker (R5, R6, R7).
- Auth is backed by DB `user_account` with three real roles; no shared admin key
  for recruiter creation (R2, R9).
- Current data is migrated with no lost candidate logins (R8).

---

## 6. Traceability
`design.md` maps each requirement to components and data models.
`tasks.md` maps each requirement ID (R1–R9) to concrete, checkable work items.
