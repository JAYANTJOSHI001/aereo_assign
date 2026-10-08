# ARCHITECTURE.md — Bulk Certificate Generator

Design document for the backend assignment. It records **what** is built, **why**
each choice was made, and **what would change** at larger scale. Keep it in sync with the code;
the README links here for the design-decision section.

---

## 1. Goals and non-goals

### Goals (all required by the assignment)
1. Accept a **bulk** request: one HTTP call, many recipients.
2. Validate recipient data; bad rows must not sink good rows.
3. Generate one certificate per valid recipient from **one predefined template**.
4. Track status/progress per job and per certificate.
5. Let the client retrieve generated certificates (individually, and as a bundle).
6. Be well tested and documented, and be explainable line by line in an interview.

### Non-goals
- Template editor / multiple designs.
- Auth, multi-tenancy, rate limiting (called out as future work).
- Distributed workers (the design leaves a clean seam for them, see section 12).

---

## 2. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Required. |
| Framework | **FastAPI** | Pydantic validation, automatic OpenAPI docs at `/docs`, simple TestClient. |
| ORM | SQLAlchemy 2.0 (sync) | Mature, explicit, works with SQLite and PostgreSQL. |
| Database | **SQLite** by default, PostgreSQL via `DATABASE_URL` | Zero-setup for reviewers; relational as required; swap is one env var. |
| Migrations | Alembic (optional) or `create_all` on startup | `create_all` is acceptable for the assignment; document the choice. |
| Certificate rendering | **ReportLab** (PDF) | Pure Python, no system dependencies, precise layout, deterministic output. |
| Background work | In-process `ThreadPoolExecutor` fed from a DB-backed job state | See section 6. |
| Tests | pytest + FastAPI `TestClient` | Standard. |
| Config | `pydantic-settings` reading env vars / `.env` | Typed, testable. |

---

## 3. High-level architecture

```
                 ┌──────────────────────────────────────────────────────┐
                 │                     FastAPI app                      │
 Client ───────▶ │  routers/        services/          workers/         │
  HTTP           │  (HTTP only) ──▶ (business logic) ─▶ (executor)      │
                 │                       │                  │           │
                 │                       ▼                  ▼           │
                 │              repositories / ORM     renderer         │
                 │                       │             (ReportLab)      │
                 └───────────────────────┼──────────────────┼───────────┘
                                         ▼                  ▼
                                  ┌────────────┐     ┌──────────────┐
                                  │ SQL DB     │     │ File storage │
                                  │ jobs,      │     │ ./storage/   │
                                  │ certificates│    │ <job>/<id>.pdf
                                  └────────────┘     └──────────────┘
```

Layering rules (enforced by review, not by tooling):

- **routers** parse HTTP, call a service, shape the response. No SQL, no rendering.
- **services** hold the business rules (create job, run job, build bundle). They own transactions.
- **renderer** is a pure function: `render_certificate(data) -> bytes`. No DB, no HTTP.
- **storage** is a tiny interface (`save`, `open`, `exists`) with a local-filesystem implementation.
- **models/schemas** are separate: ORM models are never returned directly from the API.

This separation is what makes the required tests easy: the renderer can be tested without a DB,
the service without HTTP, and the failure path by swapping the renderer for one that raises.

---

## 4. Project layout

```
bulk-certificates/
├── AGENTS.md
├── ARCHITECTURE.md
├── README.md
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py                # app factory, lifespan (startup recovery), router mounting
│   ├── config.py              # Settings (DATABASE_URL, STORAGE_DIR, MAX_RECIPIENTS, WORKERS, EAGER)
│   ├── db.py                  # engine, SessionLocal, Base, get_db dependency
│   ├── models.py              # Job, Certificate
│   ├── schemas.py             # Pydantic request/response models
│   ├── api/
│   │   ├── jobs.py            # POST /jobs, GET /jobs/{id}, list certs, bundle download
│   │   └── certificates.py    # GET /certificates/{id}, /download
│   ├── services/
│   │   ├── job_service.py     # create_job, get_progress, recover_incomplete_jobs
│   │   ├── processor.py       # process_job(job_id): the per-certificate loop
│   │   └── bundle.py          # ZIP streaming of successful certificates
│   ├── rendering/
│   │   ├── template.py        # THE one predefined template (layout constants)
│   │   └── renderer.py        # render_certificate(CertificateData) -> bytes (PDF)
│   ├── storage.py             # LocalStorage
│   └── workers.py             # executor + submit(job_id); eager mode for tests
└── tests/
    ├── conftest.py            # temp DB, temp storage dir, eager executor, client fixture
    ├── test_create_job.py
    ├── test_validation.py
    ├── test_rendering.py
    ├── test_job_status.py
    ├── test_failure_isolation.py
    └── test_retrieval.py
```

---

## 5. Data model

### `jobs`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID (str, PK) | Public identifier. Not sequential, so jobs are not enumerable. |
| `status` | enum | `pending`, `processing`, `completed`, `completed_with_errors`, `failed` |
| `title` | str | Certificate title, e.g. "Certificate of Completion". |
| `course_name` | str | Event/course the certificate is for. |
| `issuer_name` | str | Organization name printed on the certificate. |
| `issue_date` | date | Printed date. Defaults to today if omitted. |
| `total_count` | int | Number of recipients submitted. |
| `idempotency_key` | str, unique, nullable | Optional duplicate-submission guard. |
| `created_at`, `started_at`, `finished_at` | datetime (UTC) | `started_at`/`finished_at` null until reached. |

### `certificates`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Public identifier. |
| `job_id` | FK → jobs.id, indexed | |
| `row_index` | int | Position in the submitted list, so the client can map results to input. |
| `recipient_name` | str, nullable | Nullable because an invalid row may have no usable name. |
| `recipient_email` | str, nullable | |
| `raw_input` | JSON | Original row exactly as submitted, kept for debugging and for error reports. |
| `status` | enum | `pending`, `success`, `failed` |
| `error_code` | str, nullable | Machine readable: `VALIDATION_ERROR`, `RENDER_ERROR`, `STORAGE_ERROR`. |
| `error_message` | str, nullable | Human readable. |
| `certificate_number` | str, unique, nullable | Generated, e.g. `CERT-2026-8F3A1C`. Printed on the PDF. |
| `file_path` | str, nullable | Relative to `STORAGE_DIR`. Never returned to clients. |
| `generated_at` | datetime, nullable | |

Indexes: `(job_id, status)` for progress aggregation and filtering; `(job_id, row_index)` unique.

### Why store invalid rows as `certificates` rows with `status=failed`
The assignment says the job status must identify **successful and failed generations**. Persisting
every submitted row (valid or not) gives one uniform report: the client gets exactly `total_count`
result rows, each with its input index, final status and error. Nothing is silently dropped.

### Why counts are derived, not stored
`succeeded` / `failed` / `pending` are computed with a `GROUP BY status` query on demand rather than
kept as incrementing columns. This avoids lost-update races and drift, at the cost of one cheap
indexed query per status request. `total_count` is stored because it is fixed at creation.

---

## 6. Processing model (sync vs background) — the key decision

### Decision
**Asynchronous, background processing.** `POST /jobs` validates, persists everything, returns
**`202 Accepted`** with a `job_id`, and generation continues in a worker. The client polls
`GET /jobs/{id}`.

### Why not synchronous
- A request with hundreds or thousands of recipients can take seconds to minutes. Holding an HTTP
  connection open risks gateway timeouts (typically 30–60 s) and ties up a server worker.
- The assignment explicitly frames this as bulk and asks for progress tracking; that only makes
  sense when the request returns before the work completes.

### Why not Celery/Redis
- It adds two extra services for reviewers to install, which raises the chance the project does not
  run on their machine. The assignment says complexity must not come at the cost of required
  functionality.
- The design isolates the worker behind one function, `workers.submit(job_id)`, so moving to a real
  queue is a small, local change (section 12).

### How it works
1. `create_job` writes the `jobs` row (`pending`) and **all** `certificates` rows in **one
   transaction** (valid rows `pending`, invalid rows already `failed`). Commit.
2. Only **after commit**, call `workers.submit(job_id)`. (Submitting before commit would let a worker
   read a job that does not exist yet.)
3. A `ThreadPoolExecutor` (size from `WORKERS`, default 4) runs `process_job(job_id)`.
4. `process_job` opens its **own DB session**, marks the job `processing`, then iterates the
   `pending` certificates:

   ```
   for cert in pending_certificates(job):
       try:
           pdf_bytes = render_certificate(data_for(cert))
           path = storage.save(job.id, cert.id, pdf_bytes)
           cert.status, cert.file_path, cert.generated_at = SUCCESS, path, now
       except Exception as exc:                 # per-item isolation
           cert.status, cert.error_code, cert.error_message = FAILED, classify(exc), str(exc)
       session.commit()                         # commit per certificate
   ```
5. After the loop, the final job status is computed from the aggregate counts and `finished_at` set.

### Why commit per certificate
- Progress becomes visible to pollers while the job runs.
- A crash mid-job loses at most one certificate of work, and the remaining `pending` rows can be
  picked up again (see recovery below).

### Failure isolation (explicit requirement)
- The `try/except` is **inside** the loop, around exactly one certificate. One bad row never stops
  the rest.
- If a failure leaves the session in a bad state, `session.rollback()` runs before recording the
  failure, so later rows are unaffected.
- A job-level crash outside the loop (e.g. DB down) is caught at the top of `process_job`, which
  marks the job `failed` if it can and logs the traceback.

### Crash / restart recovery
In-process workers die with the process. On application startup (FastAPI lifespan),
`recover_incomplete_jobs()` finds jobs in `pending` or `processing` and re-submits them.
`process_job` only touches certificates still `pending`, so re-running is **idempotent**: finished
work is never redone. This turns "in-process threads are not durable" from a flaw into a documented,
tested property.

### Testing mode
Setting `EAGER=true` makes `workers.submit` run `process_job` inline. Tests then get deterministic
behaviour with no sleeps or polling. Production default is `EAGER=false`.

---

## 7. Job status state machine

```
 pending ──▶ processing ──▶ completed                 (all succeeded)
                        ├─▶ completed_with_errors      (≥1 succeeded AND ≥1 failed)
                        └─▶ failed                     (0 succeeded)
```

- `pending`: persisted, not yet picked up.
- `processing`: worker running; `progress` reflects live counts.
- Terminal states are never left once reached.
- A job where **every** recipient was invalid goes straight to `failed` (it is still created and
  inspectable, which is friendlier than a bare 4xx for a 1,000-row upload).

Progress object returned with every status call:

```json
{
  "total": 120, "succeeded": 95, "failed": 5, "pending": 20,
  "percent_complete": 83.3
}
```
`percent_complete = (succeeded + failed) / total * 100`, rounded to 1 decimal.

---

## 8. Validation strategy

Validation happens at **two levels**, deliberately.

### Level 1 — request-level (reject the whole request, `422`)
Malformed requests where nothing sensible can be processed:
- Body is not valid JSON or fails the schema.
- `recipients` missing, not a list, empty, or longer than `MAX_RECIPIENTS` (default 1000) → `422`
  (or `413` for oversize; pick one and document it).
- Required job fields missing/blank (`course_name`, `issuer_name`, `title`).

### Level 2 — recipient-level (accept the job, record the row as failed)
Each element of `recipients` is validated **individually** (the request schema types it as a list of
loose objects, then each is passed through a strict `RecipientIn` Pydantic model inside a
`try/except ValidationError`). Rules:
- `name`: required, trimmed, 1–100 chars, no control characters.
- `email`: required, valid format (`EmailStr`). Normalised to lowercase.
- Duplicate `email` within the same job: the **second and later** occurrences are marked failed
  with `DUPLICATE_RECIPIENT` (the first wins). Chosen so a double-pasted row does not produce two
  certificates; documented as a decision and easy to flip.
- Unknown extra fields are ignored (not an error).

Invalid rows get `status=failed`, `error_code=VALIDATION_ERROR`, and a message listing the field
problems, e.g. `"email: value is not a valid email address"`.

### Why two levels
Rejecting a 1,000-row upload because row 742 has a typo is hostile. But an unreadable body cannot be
partially processed. Splitting the two cases matches how real bulk APIs behave and satisfies "invalid
recipient data handled appropriately" **and** "one failure must not prevent others".

### Rendering safety
- Names are drawn as text through ReportLab's canvas (not interpreted as markup), so `<b>`/`&`
  characters are safe. If using `Paragraph`, escape with `xml.sax.saxutils.escape`.
- Long names are shrunk to fit (reduce font size down to a floor, then truncate with an ellipsis)
  instead of overflowing the page.
- Unicode names require a Unicode TTF (e.g. DejaVu Sans or Noto Sans bundled in `assets/fonts/`),
  registered once at import. The built-in Helvetica cannot render many non-Latin characters; a
  glyph failure is caught as a per-certificate `RENDER_ERROR`.

---

## 9. Certificate template

- Single template, defined entirely in `rendering/template.py` (page size A4 landscape, margins,
  colours, font sizes, positions) so the design is data, not scattered magic numbers.
- Content: issuer name, certificate title, "This is to certify that", **recipient name**, "has
  successfully completed", **course name**, issue date, **certificate number**.
- Output: PDF bytes. Rendering is deterministic for the same input apart from the generated
  certificate number, which makes tests stable (tests assert on text extracted from the PDF and on the
  `%PDF` header, not on byte equality).
- `certificate_number` format: `CERT-<YYYY>-<8 hex of uuid4>`; uniqueness is guaranteed by the DB
  unique constraint.

---

## 10. API design

Base path `/api/v1`. All timestamps ISO-8601 UTC. IDs are UUID strings.

### `POST /api/v1/jobs` → `202 Accepted`

Request:
```json
{
  "title": "Certificate of Completion",
  "course_name": "Advanced Python Bootcamp",
  "issuer_name": "Acme Academy",
  "issue_date": "2026-10-01",
  "recipients": [
    {"name": "Asha Verma", "email": "asha@example.com"},
    {"name": "Rohan Singh", "email": "rohan@example.com"}
  ]
}
```
Optional header: `Idempotency-Key: <string>`. Re-sending the same key returns the **original job**
with `200` instead of creating a duplicate.

Response:
```json
{
  "job_id": "7c1f...",
  "status": "pending",
  "total": 2,
  "status_url": "/api/v1/jobs/7c1f...",
  "created_at": "2026-10-08T10:00:00Z"
}
```
Headers: `Location: /api/v1/jobs/{job_id}`.

### `GET /api/v1/jobs/{job_id}` → `200`
```json
{
  "job_id": "7c1f...",
  "status": "completed_with_errors",
  "title": "...", "course_name": "...", "issuer_name": "...", "issue_date": "...",
  "progress": {"total": 3, "succeeded": 2, "failed": 1, "pending": 0, "percent_complete": 100.0},
  "created_at": "...", "started_at": "...", "finished_at": "...",
  "failures": [
    {"row_index": 2, "recipient_name": null, "recipient_email": "not-an-email",
     "error_code": "VALIDATION_ERROR", "error_message": "email: value is not a valid email address"}
  ]
}
```
`failures` is capped (e.g. first 100) with `failures_truncated: true`; the full list comes from the
filtered certificates endpoint. `404` for an unknown job.

### `GET /api/v1/jobs/{job_id}/certificates` → `200`
Query params: `status` (`success|failed|pending`), `limit` (default 50, max 200), `offset`.
Each item: `certificate_id`, `row_index`, `recipient_name`, `recipient_email`, `status`,
`certificate_number`, `error_code`, `error_message`, `download_url` (only when `success`),
`generated_at`. Response includes `total` for pagination.

### `GET /api/v1/certificates/{certificate_id}/download` → `200 application/pdf`
- `Content-Disposition: attachment; filename="<certificate_number>.pdf"`.
- `404` if unknown; `409` if the certificate is not `success` (not ready or failed, with a clear body).

### `GET /api/v1/jobs/{job_id}/download` → `200 application/zip`
ZIP of all **successful** certificates, filenames `<row_index>_<sanitised_name>.pdf`.
- If the job is still running, return what exists so far with an `X-Job-Status` header, **or** `409`
  while `pending|processing`. Decision: **`409` until terminal** — simpler contract, no partial
  bundles confusing the client. The per-certificate download works at any time.
- Zip is built by streaming from storage (`zipfile` into a `SpooledTemporaryFile`/streaming response)
  so memory does not grow with job size.

### `GET /health` → `200 {"status":"ok"}`

### Error shape (consistent across the API)
```json
{"error": {"code": "JOB_NOT_FOUND", "message": "Job 7c1f... not found"}}
```
Pydantic validation errors are translated into the same shape with `code: "VALIDATION_ERROR"` and a
`details` list.

### HTTP status summary
| Case | Status |
|---|---|
| Job accepted | 202 |
| Idempotent replay | 200 |
| Malformed/invalid request body | 422 |
| Unknown job/certificate | 404 |
| Certificate/bundle not ready or failed | 409 |

---

## 11. Storage and security

- Files live at `STORAGE_DIR/<job_id>/<certificate_id>.pdf`. Names derive from **server-generated
  UUIDs only**; recipient-supplied text never touches a filesystem path. This removes path-traversal
  risk entirely.
- Clients never see `file_path`; downloads go through the API by certificate id.
- ZIP entry names are sanitised (`re.sub(r"[^A-Za-z0-9._-]", "_", name)` and length-limited).
- UUID identifiers prevent guessing sequential ids (there is no auth, so unguessable ids are the only
  access barrier; this limitation is stated in the README).
- Request body size is bounded by `MAX_RECIPIENTS` and per-field length limits.
- No secrets in the repo; config via env vars, `.env.example` committed, `.env` git-ignored.

---

## 12. Scaling path (what changes, what does not)

| Pressure | Change | Code impact |
|---|---|---|
| Multiple API instances / durability | Replace `ThreadPoolExecutor` with Celery/RQ/Arq + Redis; `workers.submit` enqueues `job_id`. | One module. `process_job` unchanged. |
| Concurrent workers on one job | Claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` (PostgreSQL) and chunk by certificate. | `process_job` query only. |
| Heavy rendering CPU | Process pool or separate worker fleet (GIL bound). | Worker module. |
| Many/large files | `Storage` implementation for S3/GCS, signed download URLs. | `storage.py` only. |
| Multi-tenant / abuse | API keys, per-client quotas, rate limiting. | New dependency + `owner_id` column. |
| Client wants push, not poll | Webhook callback URL on the job, called at terminal state. | Small addition after `finish_job`. |

Because the router never imports the executor or the renderer, and the processor only depends on
`render_certificate` and `Storage`, each row above stays a local change.

---

## 13. Concurrency details (so the explanation holds up in an interview)

- Each worker thread uses its **own** SQLAlchemy session; sessions are never shared across threads.
- SQLite: enable `check_same_thread=False` for the engine and set `PRAGMA journal_mode=WAL` and
  `busy_timeout` so a polling reader does not block the writing worker. With PostgreSQL this is a
  non-issue.
- `process_job` is idempotent and re-entrant: it selects only `pending` certificates, so a duplicate
  submission (e.g. recovery racing a normal run) cannot double-generate. A job-level guard
  (`UPDATE jobs SET status='processing' WHERE id=:id AND status='pending'`, check rows affected) makes
  only one worker start a given job.
- Executor shutdown is handled in the lifespan teardown (`shutdown(wait=True, cancel_futures=False)`
  for graceful finish, or `wait=False` with recovery on next boot).

---

## 14. Testing strategy

Fixtures (`conftest.py`): fresh SQLite DB per test (in-memory or tmp file), temp `STORAGE_DIR`,
`EAGER=true`, FastAPI `TestClient`.

| Required area | Test file | Key assertions |
|---|---|---|
| Creating a job | `test_create_job.py` | 202, `job_id`, `Location` header, rows persisted == recipients, job reaches terminal state under eager mode, idempotency key replay returns same job. |
| Input validation | `test_validation.py` | Empty list → 422; missing fields → 422; oversize → 422; bad email row → job accepted and that row `failed` with `VALIDATION_ERROR`; blank name; duplicate email; unicode/HTML-ish names accepted safely. |
| Certificate generation | `test_rendering.py` | Output starts with `%PDF`; text extracted (`pypdf`) contains recipient name, course name, certificate number; very long name does not raise; file exists in storage. |
| Job status / progress | `test_job_status.py` | Counts sum to total; status `completed` / `completed_with_errors` / `failed` for the three scenarios; unknown id → 404; pending job shows `0%` (create job with executor disabled, assert state, then run). |
| Individual failure | `test_failure_isolation.py` | Monkeypatch `render_certificate` to raise for one specific recipient; assert others `success`, that one `failed` with `RENDER_ERROR`, job `completed_with_errors`, later rows still generated. |
| Retrieval | `test_retrieval.py` | Download returns PDF bytes + correct headers; failed cert → 409; unknown → 404; ZIP contains exactly the successful certs; ZIP for non-terminal job → 409; list endpoint filtering and pagination. |
| Recovery (bonus) | `test_job_status.py` | Simulate leftover `processing` job with some `pending` rows, run `recover_incomplete_jobs`, verify completion without regenerating finished rows. |

Run: `pytest -q` (and `pytest --cov=app` as an optional quality signal).

---

## 15. Observability

- Structured logs via `logging` (job id and certificate id in every message). Log at INFO for job
  start/finish with counts and duration, WARNING for per-certificate failures, ERROR with traceback
  for job-level crashes.
- `GET /health` for liveness. (A readiness check that queries the DB is a cheap extension.)

---

## 16. Key decisions and trade-offs (cheat sheet for the interview)

1. **Async 202 + polling** over synchronous: timeouts and bulk framing. Trade-off: client must poll.
2. **In-process threads over Celery**: zero extra infrastructure; durability recovered through DB
   state plus startup recovery. Trade-off: not horizontally scalable; documented migration path.
3. **Persist every row, including invalid ones**: a complete, uniform per-row report. Trade-off:
   a few extra rows for bad input.
4. **Two-level validation**: malformed request is rejected, bad rows are isolated.
5. **Derived counts, not counters**: no race conditions. Trade-off: one aggregate query per status call.
6. **Commit per certificate**: live progress and bounded crash loss. Trade-off: more commits
   (negligible versus PDF rendering cost).
7. **UUID-named files, never user text in paths**: eliminates path traversal.
8. **PDF via ReportLab**: no system dependencies, so the project runs anywhere `pip` works.
9. **Duplicate emails within a job fail the later rows**: prevents accidental double issuance; easy to
   change if requirements shift.
10. **ZIP is `409` until the job is terminal**: simpler client contract than partial bundles.

### Likely "changed requirement" questions and where to change them
- *"Also email each certificate."* → add a post-success step in `process_job` (or a second job type);
  new column `email_status`.
- *"Allow retrying failed rows."* → `POST /jobs/{id}/retry` resets `failed` (non-validation) rows to
  `pending` and re-submits; `process_job` already only handles `pending`.
- *"Add a second design."* → `template.py` becomes a registry keyed by `template_id`; add a job column.
- *"Make it PNG."* → swap `renderer.py`; storage and API untouched (change content type/extension).
- *"Cap job size per client."* → `MAX_RECIPIENTS` check already centralised in the schema.
