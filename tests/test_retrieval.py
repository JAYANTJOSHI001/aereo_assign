"""Tests for certificate PDF download and job ZIP bundle streaming."""

import io
import uuid
import zipfile
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from app import workers
from app.models import Job, JobStatus


def test_download_single_certificate_success(client: TestClient) -> None:
    """Verify single certificate download returns valid PDF bytes with Content-Disposition."""
    payload = {
        "title": "Diploma in Computer Science",
        "course_name": "Operating Systems & Kernels",
        "issuer_name": "Aereo Tech Institute",
        "recipients": [
            {"name": "Elena Rostova", "email": "elena@example.com"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    # Retrieve certificate list to get certificate_id
    list_resp = client.get(f"/api/v1/jobs/{job_id}/certificates")
    assert list_resp.status_code == 200
    cert_item = list_resp.json()["items"][0]
    cert_id = cert_item["certificate_id"]
    cert_number = cert_item["certificate_number"]

    # Download certificate
    dl_resp = client.get(f"/api/v1/certificates/{cert_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"] == "application/pdf"
    assert f'filename="{cert_number}.pdf"' in dl_resp.headers["content-disposition"]
    assert dl_resp.content.startswith(b"%PDF-")

    # Read PDF text
    reader = PdfReader(io.BytesIO(dl_resp.content))
    assert len(reader.pages) == 1
    page_text = reader.pages[0].extract_text()
    assert "Elena Rostova" in page_text
    assert cert_number in page_text


def test_download_failed_certificate_returns_409(client: TestClient) -> None:
    """Verify downloading a failed certificate returns 409 Conflict."""
    payload = {
        "title": "Failed Cert Test",
        "course_name": "System Security",
        "issuer_name": "Aereo Cyber",
        "recipients": [
            {"name": "Bad Email User", "email": "invalid-email"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    list_resp = client.get(f"/api/v1/jobs/{job_id}/certificates")
    cert_id = list_resp.json()["items"][0]["certificate_id"]

    dl_resp = client.get(f"/api/v1/certificates/{cert_id}/download")
    assert dl_resp.status_code == 409
    data = dl_resp.json()
    assert "error" in data
    assert data["error"]["code"] == "CONFLICT"


def test_download_unknown_certificate_returns_404(client: TestClient) -> None:
    """Verify downloading a non-existent certificate returns 404."""
    dl_resp = client.get(f"/api/v1/certificates/{uuid.uuid4()}/download")
    assert dl_resp.status_code == 404
    data = dl_resp.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"


def test_download_job_bundle_zip_success(client: TestClient) -> None:
    """Verify terminal job ZIP bundle contains exactly the successful certificates."""
    payload = {
        "title": "DevOps Professional",
        "course_name": "Kubernetes Administration",
        "issuer_name": "Aereo DevOps Academy",
        "recipients": [
            {"name": "David Miller", "email": "david@example.com"},
            {"name": "Sarah Connor", "email": "sarah@example.com"},
            {"name": "Invalid Row", "email": "not-an-email"},  # failed row
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    # Download ZIP bundle
    zip_resp = client.get(f"/api/v1/jobs/{job_id}/download")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"
    assert f'filename="job_{job_id}_certificates.zip"' in zip_resp.headers["content-disposition"]

    # Verify ZIP archive contents
    with zipfile.ZipFile(io.BytesIO(zip_resp.content), "r") as zf:
        namelist = zf.namelist()
        # Exactly 2 successful certificates
        assert len(namelist) == 2
        assert "0_David_Miller.pdf" in namelist
        assert "1_Sarah_Connor.pdf" in namelist

        for name in namelist:
            file_bytes = zf.read(name)
            assert file_bytes.startswith(b"%PDF-")
            reader = PdfReader(io.BytesIO(file_bytes))
            assert len(reader.pages) == 1


def test_download_job_bundle_returns_409_while_pending(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify downloading bundle for a non-terminal (pending) job returns 409 Conflict."""
    # Defer worker execution to keep job in pending state
    monkeypatch.setattr(workers, "submit", lambda job_id: None)

    payload = {
        "title": "Pending Job Test",
        "course_name": "Distributed Algorithms",
        "issuer_name": "Aereo Research",
        "recipients": [
            {"name": "Grace Hopper", "email": "grace@example.com"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    zip_resp = client.get(f"/api/v1/jobs/{job_id}/download")
    assert zip_resp.status_code == 409
    data = zip_resp.json()
    assert "error" in data
    assert data["error"]["code"] == "CONFLICT"


def test_download_job_bundle_unknown_job_404(client: TestClient) -> None:
    """Verify downloading bundle for an unknown job returns 404."""
    zip_resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/download")
    assert zip_resp.status_code == 404
    data = zip_resp.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"
