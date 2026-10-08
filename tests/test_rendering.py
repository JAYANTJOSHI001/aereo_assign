"""Unit tests for PDF certificate rendering and file storage."""

from datetime import date
import io
import uuid
from pathlib import Path
import pytest
from pypdf import PdfReader

from app.rendering.renderer import CertificateData, render_certificate
from app.storage import LocalStorage


def test_pdf_rendering_header_and_content() -> None:
    """Verify generated PDF starts with %PDF and contains all specified fields."""
    data = CertificateData(
        issuer_name="Aereo Academy",
        title="Certificate of Achievement",
        recipient_name="Johnathon Doe",
        course_name="Cloud Native Python Microservices",
        issue_date=date(2026, 10, 8),
        certificate_number="CERT-2026-A1B2C3D4",
    )

    pdf_bytes = render_certificate(data)
    assert pdf_bytes.startswith(b"%PDF-")

    # Read PDF text using pypdf
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    page_text = reader.pages[0].extract_text()

    assert "AEREO ACADEMY" in page_text or "Aereo Academy" in page_text
    assert "Certificate of Achievement" in page_text
    assert "Johnathon Doe" in page_text
    assert "Cloud Native Python Microservices" in page_text
    assert "CERT-2026-A1B2C3D4" in page_text
    assert "2026-10-08" in page_text


def test_rendering_very_long_name_shrink_and_truncate() -> None:
    """Verify extremely long name does not raise and is safely shrunk/truncated."""
    long_name = "Sir Christopher Alexander Montgomery-Fotheringhay-Smythe III of Wellington-on-Thames"
    data = CertificateData(
        issuer_name="National Engineering Institute",
        title="Certificate of Completion",
        recipient_name=long_name,
        course_name="High-Performance Systems Design",
        issue_date="2026-10-08",
        certificate_number="CERT-2026-LONGNAME",
    )

    pdf_bytes = render_certificate(data)
    assert pdf_bytes.startswith(b"%PDF")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_text = reader.pages[0].extract_text()
    assert "..." in page_text or "Christopher" in page_text


def test_rendering_unicode_and_non_latin_names() -> None:
    """Verify Unicode / non-Latin characters render without exception."""
    test_cases = [
        "François Müller",
        "Владимир Смирнов",
        "Aarav Sharma",
    ]
    for name in test_cases:
        data = CertificateData(
            issuer_name="Global Learning Trust",
            title="Certificate of Excellence",
            recipient_name=name,
            course_name="International Python Workshop",
            issue_date=date(2026, 10, 8),
            certificate_number=f"CERT-{uuid.uuid4().hex[:8].upper()}",
        )
        pdf_bytes = render_certificate(data)
        assert pdf_bytes.startswith(b"%PDF")
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) == 1


def test_local_storage_roundtrip(tmp_path: Path) -> None:
    """Verify LocalStorage saves, checks existence, and opens files accurately."""
    store = LocalStorage(base_dir=tmp_path / "certs")
    job_id = str(uuid.uuid4())
    cert_id = str(uuid.uuid4())

    payload = b"%PDF-1.4 test certificate binary content"
    rel_path = store.save(job_id, cert_id, payload)

    assert rel_path == f"{job_id}/{cert_id}.pdf"
    assert store.exists(rel_path) is True
    assert (tmp_path / "certs" / rel_path).exists()

    with store.open(rel_path) as f:
        read_back = f.read()
    assert read_back == payload

    # Non-existent file check
    assert store.exists(f"{job_id}/non_existent.pdf") is False
    with pytest.raises(FileNotFoundError):
        store.open(f"{job_id}/non_existent.pdf")


def test_local_storage_uuid_enforcement(tmp_path: Path) -> None:
    """Verify that storage rejects non-UUID identifiers preventing path traversal."""
    store = LocalStorage(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store.save("../../etc", "cert-1", b"data")

    with pytest.raises(ValueError):
        store.save(str(uuid.uuid4()), "../passwd", b"data")
