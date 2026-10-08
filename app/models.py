"""SQLAlchemy database models for Jobs and Certificates."""

import enum
from typing import Any, Optional
import uuid
from datetime import date, datetime, timezone
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


class UTCDateTime(TypeDecorator):
    """Ensure stored and retrieved datetimes are timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class JobStatus(str, enum.Enum):
    """Lifecycle status states for a certificate generation job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class CertificateStatus(str, enum.Enum):
    """Processing status for an individual recipient certificate."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class Job(Base):
    """Job tracking bulk certificate generation request."""

    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(
        Enum(JobStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    title = Column(String(255), nullable=False)
    course_name = Column(String(255), nullable=False)
    issuer_name = Column(String(255), nullable=False)
    issue_date = Column(Date, nullable=False, default=lambda: datetime.now(timezone.utc).date())
    total_count = Column(Integer, nullable=False)
    idempotency_key = Column(String(255), unique=True, nullable=True, index=True)

    created_at = Column(
        UTCDateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at = Column(UTCDateTime, nullable=True)
    finished_at = Column(UTCDateTime, nullable=True)

    certificates = relationship(
        "Certificate",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Certificate.row_index",
    )


class Certificate(Base):
    """Individual certificate row for a recipient within a job."""

    __tablename__ = "certificates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    row_index = Column(Integer, nullable=False)

    recipient_name = Column(String(255), nullable=True)
    recipient_email = Column(String(255), nullable=True)
    raw_input = Column(JSON, nullable=False)

    status = Column(
        Enum(CertificateStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=CertificateStatus.PENDING,
        index=True,
    )
    error_code = Column(String(100), nullable=True)
    error_message = Column(String(1000), nullable=True)

    certificate_number = Column(String(50), unique=True, nullable=True, index=True)
    file_path = Column(String(500), nullable=True)
    generated_at = Column(UTCDateTime, nullable=True)

    job = relationship("Job", back_populates="certificates")

    __table_args__ = (
        Index("ix_certificates_job_status", "job_id", "status"),
        UniqueConstraint("job_id", "row_index", name="uq_certificates_job_row_index"),
    )
