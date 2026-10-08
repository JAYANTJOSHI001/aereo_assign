# AGENTS.md — Instructions for AI coding agents

You are implementing the **Bulk Certificate Generator** backend assignment end to end.
Read this file fully, then read `ARCHITECTURE.md`. **`ARCHITECTURE.md` is the source of truth for design;
this file is the source of truth for process, standards and done-criteria.** If they conflict, stop
and ask the human, or note the conflict in the README and follow `ARCHITECTURE.md`.

The human who owns this code must be able to **explain and modify every line in an interview**. Write
simple, readable code over clever code. Prefer the standard library and well-known libraries.

---

## 1. The assignment in one paragraph

A Python backend (FastAPI) with a relational DB. A client submits **one request containing many
recipients**; the system validates them, generates a PDF certificate per valid recipient from **one
predefined template**, tracks per-job and per-certificate status, and lets the client retrieve the
certificates. A failure on one certificate must not stop the others. Include tests, and a README.

---

## 2. Non-negotiable requirements (checklist)

Treat each as a release gate. Tick them off in the final report.

- [ ] Python + **FastAPI** + relational DB (SQLite default, PostgreSQL-compatible).
- [ ] **Bulk** endpoint: one request, many recipients (not one request per certificate).
- [ ] Recipient validation; invalid rows are reported, not silently dropped.
- [ ] Certificates generated from **one predefined template**, containing recipient-specific data.
- [ ] Job status/progress endpoint with **successful and failed** items identifiable.
- [ ] One certificate failing does **not** block other certificates in the same job.
- [ ] Generated certificates retrievable (single download; ZIP bundle is a bonus but planned).
- [ ] Processing choice (sync vs background) **documented with reasoning** in the README.
- [ ] Tests for: job creation, input validation, certificate generation, job status/progress,
      individual failure handling, certificate retrieval.
- [ ] README: setup, run, test, submit a request (with curl), retrieve certificates, design decisions.

Optional work (idempotency key, recovery, ZIP, pagination) is allowed **only after** every box above
is ticked and tests pass.

---

## 3. Fixed technology decisions (do not re-litigate)

| Item | Decision |
|---|---|
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0, sync sessions |
| DB | SQLite by default; `DATABASE_URL` env var for PostgreSQL |
| PDF | ReportLab |
| Processing | Background `ThreadPoolExecutor`, `202 Accepted` + polling (see `ARCHITECTURE.md` §6) |
| Config | `pydantic-settings` |
| Tests | pytest + FastAPI `TestClient` (+ `pypdf` to read PDFs back) |
| Python | 3.11+ |

Do **not** add Celery, Redis, Docker-only workflows, auth, or a front end unless the human asks.
Docker/Dockerfile is acceptable as an extra only if the non-Docker path still works.

---

## 4. Setup and commands (must work exactly as written in the README)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --reload        # run  → http://127.0.0.1:8000/docs
pytest -q                            # tests
pytest --cov=app                     # optional coverage
```

`requirements.txt` (pin loosely with `>=`, or pin exact after verifying):
```
fastapi
uvicorn[standard]
sqlalchemy>=2.0
pydantic>=2
pydantic-settings
email-validator
reportlab
pypdf
pytest
httpx
pytest-cov
```

`.env.example`:
```
DATABASE_URL=sqlite:///./data/app.db
STORAGE_DIR=./storage
MAX_RECIPIENTS=1000
WORKERS=4
EAGER=false
LOG_LEVEL=INFO
```

---

## 5. Build order (milestones)

Work **in this order**. After every milestone run the tests and make a small, meaningful commit.
Do not start the next milestone while the current one is red.

### M0 — Skeleton
- Create the layout from `ARCHITECTURE.md` §4, `requirements.txt`, `.env.example`, `.gitignore`
  (ignore `.venv`, `.env`, `storage/`, `data/`, `__pycache__`, `.pytest_cache`).
- `config.py`, `db.py`, `main.py` with `GET /health`.
- **Done when:** app starts; `/health` returns `{"status":"ok"}`; a trivial test passes.

### M1 — Models and schemas
- `models.py`: `Job`, `Certificate` exactly as in `ARCHITECTURE.md` §5 (enums as `str` Enums).
- `schemas.py`: `JobCreate` (loose `recipients: list[dict]`), strict `RecipientIn`, response models.
- Create tables on startup (`Base.metadata.create_all`).
- **Done when:** a test can insert and read back a job with certificates.

### M2 — Renderer (pure function)
- `rendering/template.py` (layout constants) and `rendering/renderer.py`
  (`render_certificate(CertificateData) -> bytes`).
- Bundle a Unicode font under `app/rendering/fonts/` (e.g. DejaVuSans) and register it; fall back to
  Helvetica only if registration fails, logging a warning.
- Shrink-to-fit for long names.
- **Done when:** `test_rendering.py` passes: `%PDF` header, `pypdf` text contains name, course, number.
- Also open one generated PDF visually (render a page to PNG or inspect) and confirm layout is sane;
  fix overlaps before moving on.

### M3 — Storage
- `storage.py` with `LocalStorage.save(job_id, cert_id, data) -> relative_path`, `open`, `exists`.
- Paths built **only** from server UUIDs. Create directories as needed.
- **Done when:** save/read round-trip test passes; no user text appears in any path.

### M4 — Job creation (`POST /api/v1/jobs`)
- `job_service.create_job`: validate each recipient individually (`ARCHITECTURE.md` §8), detect
  duplicate emails, persist job + **all** certificate rows in **one transaction**, commit, **then**
  call `workers.submit(job_id)`.
- Return `202` with `job_id`, `status`, `total`, `status_url`, `Location` header.
- Handle optional `Idempotency-Key`.
- Global exception handler producing the standard error shape.
- **Done when:** `test_create_job.py` and `test_validation.py` pass.

### M5 — Processor and worker
- `workers.py`: executor, `submit`, eager mode when `EAGER=true`.
- `processor.process_job`: own session, atomic `pending→processing` claim, per-certificate
  `try/except` with rollback + commit per item, final status from aggregates, `finished_at`.
- Lifespan hook: `recover_incomplete_jobs()` on startup, graceful executor shutdown.
- **Done when:** `test_failure_isolation.py` passes (one injected failure, the rest succeed).

### M6 — Status and listing
- `GET /api/v1/jobs/{id}` with `progress` and capped `failures`.
- `GET /api/v1/jobs/{id}/certificates` with `status`, `limit`, `offset`.
- **Done when:** `test_job_status.py` passes for completed / completed_with_errors / failed / pending
  / unknown-id.

### M7 — Retrieval
- `GET /api/v1/certificates/{id}/download` (PDF, `Content-Disposition`).
- `GET /api/v1/jobs/{id}/download` (ZIP of successful certs; `409` until terminal).
- **Done when:** `test_retrieval.py` passes.

### M8 — Hardening and docs
- Logging (job/cert ids in messages), SQLite WAL + busy timeout, input length limits.
- Write `README.md` (section 8 below).
- Run the end-to-end smoke test (section 9) against a real running server.
- **Done when:** every checklist item in section 2 is ticked.

---

## 6. Coding standards

- Type hints on all function signatures. Use `datetime.now(timezone.utc)`, never naive datetimes.
- Routers contain **no** SQL and **no** rendering. Services contain business logic. The renderer has
  **no** DB or HTTP imports. (`ARCHITECTURE.md` §3)
- ORM models are never returned directly; always map through response schemas.
- One DB session per request (dependency) and one per worker run. **Never share a session across
  threads.**
- Catch **specific** exceptions where you can; the single deliberate broad `except Exception` is the
  per-certificate boundary in `process_job`, and it must log the traceback and record the error.
- Never build file paths from recipient-supplied text. Never log full request bodies (PII).
- No `print`; use `logging`.
- Functions short and single-purpose; docstring on every public function stating *why*, not just *what*.
- Comments explain non-obvious decisions (e.g. "commit before submit so the worker can see the job").
- No dead code, no commented-out blocks, no TODOs left in submitted code.
- Format with `ruff format` / `black` if available; keep lines ≤ 100 chars.

---

## 7. Testing rules

- Every required area in section 2 has at least one dedicated test file (names in
  `ARCHITECTURE.md` §14).
- Tests must be **deterministic**: use `EAGER=true`, no `sleep`, no network, no dependence on test
  order. Each test gets a fresh DB and a temp storage dir (`tmp_path`).
- Inject failures via `monkeypatch` on `render_certificate` (do not add test-only branches to
  production code, except the documented `EAGER` setting).
- Assert **behaviour and persisted state**, not just status codes: check DB rows, files on disk, and
  PDF text content.
- Include negative tests (404, 409, 422) and at least one test that a failed row does not appear as
  downloadable.
- Before declaring done: `pytest -q` is fully green, run twice to catch order or state leakage.

---

## 8. README requirements

Sections, in this order:
1. **Overview** (what it does, one paragraph).
2. **Tech stack**.
3. **Setup** (venv, install, env file).
4. **Run** (uvicorn command, `/docs` URL).
5. **Run tests**.
6. **API usage** with copy-pasteable `curl` for: submit job, poll status, list certificates, download
   one certificate (`-o cert.pdf`), download the ZIP. Include a sample payload that has one **invalid**
   recipient so the failure reporting is visible.
7. **Status values and error codes** table.
8. **Design decisions**: processing model (sync vs background, with reasoning), validation strategy,
   failure isolation, storage, derived counts. Link to `ARCHITECTURE.md`.
9. **Limitations and future work** (no auth, in-process workers, local storage; scaling path).

Every command in the README must be run by you once and confirmed working before finishing.

---

## 9. End-to-end smoke test (run before finishing)

1. Start the server: `uvicorn app.main:app`.
2. Submit a job with 5 recipients: 3 valid, 1 invalid email, 1 duplicate email.
   Expect `202` and a `job_id`.
3. Poll `GET /api/v1/jobs/{id}` until terminal. Expect `completed_with_errors`, progress
   `succeeded=3, failed=2`, and `failures` listing rows with error codes.
4. List certificates with `?status=success`; download one; confirm it is a valid PDF showing the
   right name.
5. Download the ZIP; confirm it contains exactly 3 PDFs.
6. Try downloading a failed certificate → expect `409`; unknown id → `404`.
7. Submit a 1,000-recipient job; confirm the request returns immediately and progress advances while
   it runs.
8. Kill the server mid-job, restart, confirm the job finishes without regenerating completed rows.

Report the actual results (not assumptions).

---

## 10. Things to avoid

- Don't generate certificates inside the request handler (breaks the 202 design).
- Don't submit to the executor **before** the DB commit.
- Don't store progress as incrementing counters; derive from `GROUP BY status`.
- Don't let one bad row raise out of the loop; don't swallow errors silently either.
- Don't return filesystem paths to clients.
- Don't over-build: no template editor, auth, front end, or message broker.
- Don't leave generated files, `.env`, `*.db`, or `storage/` in version control.
- Don't claim something works without having run it.

---

## 11. Interview readiness (deliverable inside the repo)

Add a short section at the end of `ARCHITECTURE.md` or README titled **"Walkthrough"** with: the
request lifecycle in 8 steps (HTTP request → DB rows → worker → files → status → download), where
each required behaviour lives (file + function), and the answers to the "changed requirement"
questions in `ARCHITECTURE.md` §16. The owner must be able to modify at least: the template layout,
the validation rules, the failure handling, and add a retry endpoint without help.

---

## 12. Definition of done

All of the following are true, and you have **evidence** for each in your final message:

1. Section 2 checklist fully ticked.
2. `pytest -q` green (include the output summary).
3. Smoke test (section 9) executed with real results.
4. README commands verified by running them.
5. No secrets, DB files, or generated certificates committed.
6. Code matches `ARCHITECTURE.md`; any deviation is listed with the reason.

Final message format: what was built, how to run it, test results, deviations/assumptions,
known limitations.
