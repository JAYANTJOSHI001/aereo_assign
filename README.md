# Bulk Certificate Generator

## 1. Overview
The Bulk Certificate Generator is a robust, asynchronous Python backend service built with FastAPI and SQLAlchemy for generating personalized PDF certificates in bulk. A client submits a single bulk request containing recipient details, which are rigorously validated per-row. Valid recipients are rendered into publication-quality A4 landscape PDF certificates from a unified template using ReportLab, while invalid rows are isolated and transparently reported without halting the batch. The system tracks granular job and certificate progress, enforces concurrency boundaries, isolates errors, and enables individual PDF retrieval as well as full ZIP bundle downloads.

---

## 2. Tech Stack
- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (0.115+) & Starlette
- **Language**: Python 3.11+
- **Database & ORM**: SQLite (default, WAL mode enabled) / PostgreSQL-compatible via SQLAlchemy 2.0 (synchronous sessions)
- **PDF Engine**: [ReportLab](https://www.reportlab.com/) (pure Python canvas rendering with bundled DejaVuSans Unicode font)
- **Asynchronous Execution**: In-process `ThreadPoolExecutor` with background job state tracking and eager test mode
- **Configuration**: `pydantic-settings` (reading `.env` and environment variables)
- **Testing**: `pytest`, `pytest-cov`, `httpx`, and `pypdf` (for PDF byte extraction validation)

---

## 3. Setup

### Clone and Create Virtual Environment
```bash
# Clone or enter project directory
cd /path/to/bulk-certificates

# Create Python virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate
```

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Configure Environment
```bash
# Copy example configuration
cp .env.example .env
# Windows (PowerShell):
Copy-Item .env.example .env
```

Default configuration in `.env`:
```ini
DATABASE_URL=sqlite:///./data/app.db
STORAGE_DIR=./storage
MAX_RECIPIENTS=1000
WORKERS=4
EAGER=false
LOG_LEVEL=INFO
```

---

## 4. Run

Start the application with Uvicorn:
```bash
uvicorn app.main:app --reload
```
- **Service URL**: `http://127.0.0.1:8000`
- **Interactive Swagger Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **OpenAPI Schema**: [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)
- **Health Check**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## 5. Run Tests

Execute the automated test suite:
```bash
# Run tests quietly
pytest -q

# Run verbose tests with coverage report
pytest --cov=app -v
```

---

## 6. API Usage

### 6.1 Submit a Bulk Certificate Job
Submit a batch of recipients. This payload deliberately includes **3 valid recipients**, **1 invalid email**, and **1 duplicate email** to demonstrate error isolation.

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: sample-batch-uuid-001" \
  -d '{
    "title": "Certificate of Completion",
    "course_name": "Distributed Systems Architecture",
    "issuer_name": "Aereo Academy",
    "issue_date": "2026-10-08",
    "recipients": [
      {"name": "Alice Baker", "email": "alice@example.com"},
      {"name": "Charlie Davis", "email": "charlie@example.com"},
      {"name": "Bad Email User", "email": "not-a-valid-email"},
      {"name": "Duplicate Charlie", "email": "charlie@example.com"},
      {"name": "Elena Rostova", "email": "elena@example.com"}
    ]
  }'
```

**Response (`202 Accepted`)**:
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "pending",
  "total": 5,
  "status_url": "/api/v1/jobs/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "created_at": "2026-10-08T11:45:00.123456Z"
}
```
*Note: Response header includes `Location: /api/v1/jobs/<job_id>`.*

---

### 6.2 Poll Job Status and Progress
Query job progress and inspect individual row errors:

```bash
curl "http://127.0.0.1:8000/api/v1/jobs/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
```

**Response (`200 OK`)**:
```json
{
  "job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "completed_with_errors",
  "title": "Certificate of Completion",
  "course_name": "Distributed Systems Architecture",
  "issuer_name": "Aereo Academy",
  "issue_date": "2026-10-08",
  "progress": {
    "total": 5,
    "succeeded": 3,
    "failed": 2,
    "pending": 0,
    "percent_complete": 100.0
  },
  "created_at": "2026-10-08T11:45:00.123456Z",
  "started_at": "2026-10-08T11:45:00.150200Z",
  "finished_at": "2026-10-08T11:45:01.420100Z",
  "failures": [
    {
      "row_index": 2,
      "recipient_name": "Bad Email User",
      "recipient_email": "not-a-valid-email",
      "error_code": "VALIDATION_ERROR",
      "error_message": "email: value is not a valid email address"
    },
    {
      "row_index": 3,
      "recipient_name": "Duplicate Charlie",
      "recipient_email": "charlie@example.com",
      "error_code": "DUPLICATE_RECIPIENT",
      "error_message": "Duplicate recipient email 'charlie@example.com' within job"
    }
  ],
  "failures_truncated": false
}
```

---

### 6.3 List Generated Certificates
Retrieve certificates with optional status filtering and pagination:

```bash
# List only successfully generated certificates
curl "http://127.0.0.1:8000/api/v1/jobs/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/certificates?status=success&limit=10&offset=0"
```

**Response (`200 OK`)**:
```json
{
  "total": 3,
  "limit": 10,
  "offset": 0,
  "items": [
    {
      "certificate_id": "e81d4a0b-1934-4bc8-8eb1-7c91db18b9b8",
      "row_index": 0,
      "recipient_name": "Alice Baker",
      "recipient_email": "alice@example.com",
      "status": "success",
      "certificate_number": "CERT-2026-A1B2C3D4",
      "error_code": null,
      "error_message": null,
      "download_url": "/api/v1/certificates/e81d4a0b-1934-4bc8-8eb1-7c91db18b9b8/download",
      "generated_at": "2026-10-08T11:45:00.850000Z"
    }
  ]
}
```

---

### 6.4 Download an Individual Certificate PDF
Download a single certificate using its `certificate_id`:

```bash
curl -L "http://127.0.0.1:8000/api/v1/certificates/e81d4a0b-1934-4bc8-8eb1-7c91db18b9b8/download" -o cert.pdf
```
*Note: Returns `409 Conflict` if the certificate is pending or failed.*

---

### 6.5 Download All Successful Certificates as a ZIP Archive
Stream a ZIP archive containing all successful certificates once the job has reached a terminal state:

```bash
curl -L "http://127.0.0.1:8000/api/v1/jobs/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/download" -o certificates.zip
```
*Note: Returns `409 Conflict` if the job is still pending or processing.*

---

## 7. Status Values and Error Codes

### Job Lifecycle Status (`JobStatus`)
| Status | Description |
|---|---|
| `pending` | Persisted to the database, queued for worker execution. |
| `processing` | Worker has claimed the job and is generating certificates. |
| `completed` | Terminal state: all recipient certificates generated successfully. |
| `completed_with_errors` | Terminal state: ≥1 certificates succeeded and ≥1 failed. |
| `failed` | Terminal state: 0 certificates succeeded (all failed). |

### Certificate Status (`CertificateStatus`)
| Status | Description |
|---|---|
| `pending` | Validated row awaiting rendering and storage. |
| `success` | Successfully rendered, stored, and assigned a unique certificate number. |
| `failed` | Validation failure, intra-job duplication, rendering error, or storage failure. |

### Error Codes
| Code | Scope | Description |
|---|---|---|
| `VALIDATION_ERROR` | Request or Recipient | Payload schema validation error (malformed JSON, invalid email, blank name). |
| `DUPLICATE_RECIPIENT` | Recipient | Second or subsequent appearance of the same email in one job. |
| `RENDER_ERROR` | Certificate | Exception occurred inside ReportLab rendering canvas. |
| `STORAGE_ERROR` | Certificate | Filesystem I/O or permissions error during PDF file writing. |
| `NOT_FOUND` | HTTP 404 | Job or Certificate ID does not exist. |
| `CONFLICT` | HTTP 409 | Attempting to download non-successful cert or non-terminal ZIP bundle. |
| `INTERNAL_SERVER_ERROR` | HTTP 500 | Unhandled system exception. |

---

## 8. Design Decisions

For full technical specifications and architectural rationale, refer to [ARCHITECTURE.md](ARCHITECTURE.md).

1. **Processing Model (Asynchronous 202 Accepted + Polling)**:
   - Bulk jobs of hundreds or thousands of recipients take seconds to minutes. Synchronous HTTP responses risk gateway timeouts (30-60s) and thread starvation.
   - Returning `202 Accepted` immediately gives the client a reliable handle (`status_url`) to poll progress without connection exhaustion.
2. **In-Process ThreadPoolExecutor Over Celery/Redis**:
   - Keeps setup self-contained with zero external daemon requirements (no Redis/RabbitMQ needed for local reviewer inspection).
   - Clean abstraction: `workers.submit(job_id)` encapsulates execution so migrating to Celery, RQ, or AWS SQS requires modifying only one module without altering business logic.
3. **Two-Level Validation Strategy**:
   - **Level 1 (Request level, 422)**: Rejects syntactically broken JSON, missing course/issuer titles, empty lists, or lists exceeding `MAX_RECIPIENTS` (1,000).
   - **Level 2 (Recipient level, per-row isolation)**: Validates each row individually through `RecipientIn`. One invalid email does not fail good recipients.
4. **Persist All Rows Uniformly**:
   - Both valid and invalid rows are stored in the `certificates` table. The client receives an exact 1:1 mapping matching their input row index with clear error diagnostic codes.
5. **Derived Progress Counts (`GROUP BY status`)**:
   - Succeeded, failed, and pending counts are derived via an indexed query rather than error-prone mutable integer counter columns, eliminating race conditions.
6. **Per-Certificate Commit & Failure Isolation**:
   - The worker wraps each certificate in an isolated `try/except` block with `db.rollback()` on error and commits after every single certificate. Progress is visible in real-time, and crashes lose at most one certificate's work.
7. **Storage Path Security**:
   - Files are stored at `<STORAGE_DIR>/<job_id>/<certificate_id>.pdf`. Paths are built **exclusively from server-generated UUIDs**, completely eliminating directory traversal attacks.
8. **Crash & Restart Recovery**:
   - Interrupted jobs in `pending` or `processing` states are recovered at startup via `recover_incomplete_jobs()`. Already generated certificates (`success` or `failed`) are skipped, ensuring idempotency.
9. **ZIP Streaming with Terminal Gating**:
   - ZIP bundles return `409 Conflict` until the job is in a terminal state, preventing ambiguous partial archives.

---

## 9. Limitations and Future Work
- **Authentication & Multi-Tenancy**: Currently open without API keys or JWT auth; security relies on unguessable UUIDs. Adding multi-tenancy would require an `owner_id` column and authentication middleware.
- **Distributed Scale**: In-process threads run on a single host. Horizontal multi-instance scale requires migrating `workers.py` to a message broker (e.g. Celery with Redis) and Postgres with `SELECT FOR UPDATE SKIP LOCKED`.
- **Cloud Storage**: Current storage is local disk (`LocalStorage`). Production deployment at scale would implement an S3/GCS adapter generating pre-signed download URLs.
- **Push Notifications / Webhooks**: Client currently polls; a webhook callback on terminal state transition would eliminate polling overhead.

---

## 10. Walkthrough & Interview Guide

### 8-Step Request Lifecycle
1. **HTTP Submission**: Client calls `POST /api/v1/jobs`. FastAPI validates request JSON shape against `JobCreate` schema.
2. **Service Validation & Persistence**: `job_service.create_job` validates required fields, checks `Idempotency-Key`, runs per-row `RecipientIn` validation, identifies duplicate emails, inserts `Job` and all `Certificate` rows into the database, and commits the transaction.
3. **HTTP 202 Response**: Server returns `202 Accepted` with `job_id`, `status: pending`, and `Location: /api/v1/jobs/{job_id}`.
4. **Worker Dispatch**: Post-commit, `workers.submit(job.id)` queues `process_job(job_id)` to the `ThreadPoolExecutor`.
5. **Atomic Claim & Iteration**: `processor.process_job` opens its own DB session, atomically claims the job (`pending` $\rightarrow$ `processing`), and queries only `pending` certificates.
6. **Isolated Rendering & Storage**: For each certificate, `renderer.render_certificate` generates PDF bytes in memory. `storage.save` writes the PDF to `<STORAGE_DIR>/<job_id>/<cert_id>.pdf`. The certificate is marked `SUCCESS` and committed. If an exception occurs, the transaction rolls back, records `FAILED` with `RENDER_ERROR`, and commits.
7. **Terminal State Computation**: Aggregate status query calculates `succeeded`, `failed`, `pending`. The job status is updated to `completed`, `completed_with_errors`, or `failed` with `finished_at`.
8. **Client Retrieval**: Client polls `GET /api/v1/jobs/{id}`, lists certificates, downloads individual PDFs via `GET /api/v1/certificates/{id}/download`, or streams the full ZIP archive via `GET /api/v1/jobs/{id}/download`.

### Where Key Behaviors Live
| Requirement | File | Function / Component |
|---|---|---|
| Request & Schema Validation | [app/schemas.py](app/schemas.py) | `JobCreate`, `RecipientIn` |
| Job Creation & Recipient Validation | [app/services/job_service.py](app/services/job_service.py) | `create_job()` |
| Background Worker Pool | [app/workers.py](app/workers.py) | `submit()`, `get_executor()` |
| Per-Item Processing & Failure Isolation | [app/services/processor.py](app/services/processor.py) | `process_job()` |
| PDF Layout & Typography | [app/rendering/template.py](app/rendering/template.py) | Layout constants & font metrics |
| Pure PDF Rendering | [app/rendering/renderer.py](app/rendering/renderer.py) | `render_certificate()` |
| Local File Storage | [app/storage.py](app/storage.py) | `LocalStorage.save()`, `open()` |
| Derived Progress & Listing | [app/services/job_service.py](app/services/job_service.py) | `get_job_detail()`, `list_job_certificates()` |
| ZIP Archive Streaming | [app/services/bundle.py](app/services/bundle.py) | `create_job_zip()` |
| Startup Recovery & Shutdown | [app/main.py](app/main.py) | `lifespan()` |

### How to Modify for Changed Requirements
- **"Also email each certificate"**:
  In `app/services/processor.py`, immediately after `storage.save` in `process_job`, call an email notification function `email_service.send_certificate(cert)`. Add `email_status` column to `Certificate` model.
- **"Allow retrying failed rows"**:
  Add endpoint `POST /api/v1/jobs/{id}/retry`. In `job_service`, reset failed non-validation certificates to `pending` (`status = 'pending'`, `error_code = null`), reset job status to `pending`, and call `workers.submit(job_id)`. `process_job` already only processes `pending` rows.
- **"Add a second certificate template design"**:
  In `app/rendering/template.py`, define templates as a registry dictionary keyed by template ID. Add a `template_id` string column to `Job` and pass template settings into `render_certificate`.
- **"Render certificates as PNG instead of PDF"**:
  Modify `app/rendering/renderer.py` using Pillow or ReportLab graphics to output PNG bytes. Update `Content-Type` to `image/png` and extension to `.png` in `app/api/certificates.py` and `storage.py`.
- **"Cap job size per client or tenant"**:
  Add `MAX_RECIPIENTS` enforcement per tier or tenant in `JobCreate.validate_recipients_list` in `app/schemas.py`.
