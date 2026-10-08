"""File storage management for generated certificates."""

from pathlib import Path
from typing import BinaryIO
import uuid

from app.config import settings


class LocalStorage:
    """Local filesystem storage for certificate documents."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._explicit_base_dir = Path(base_dir).resolve() if base_dir else None

    @property
    def base_dir(self) -> Path:
        """Resolve storage base directory dynamically from settings unless explicitly set."""
        target = self._explicit_base_dir or Path(settings.STORAGE_DIR).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _validate_uuid(self, val: str) -> str:
        """Ensure the identifier is a valid UUID string to prevent path traversal."""
        parsed = uuid.UUID(str(val))
        return str(parsed)

    def save(self, job_id: str, certificate_id: str, data: bytes) -> str:
        """
        Save certificate PDF data under <base_dir>/<job_id>/<certificate_id>.pdf.

        Returns:
            Relative path string formatted as '<job_id>/<certificate_id>.pdf'.
        """
        clean_job_id = self._validate_uuid(job_id)
        clean_cert_id = self._validate_uuid(certificate_id)

        job_dir = self.base_dir / clean_job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        rel_path = f"{clean_job_id}/{clean_cert_id}.pdf"
        file_path = self.base_dir / rel_path
        file_path.write_bytes(data)
        return rel_path

    def open(self, relative_path: str) -> BinaryIO:
        """
        Open a stored certificate file for reading in binary mode.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        target_path = (self.base_dir / relative_path).resolve()
        # Verify target is strictly within base_dir
        if not target_path.is_relative_to(self.base_dir):
            raise PermissionError("Path traversal attempt detected")
        if not target_path.is_file():
            raise FileNotFoundError(f"Certificate file not found: {relative_path}")
        return open(target_path, "rb")

    def exists(self, relative_path: str) -> bool:
        """Check whether the given certificate file exists in storage."""
        target_path = (self.base_dir / relative_path).resolve()
        if not target_path.is_relative_to(self.base_dir):
            return False
        return target_path.is_file()

    def get_absolute_path(self, relative_path: str) -> Path:
        """Get absolute Path object safely verified within base_dir."""
        target_path = (self.base_dir / relative_path).resolve()
        if not target_path.is_relative_to(self.base_dir):
            raise PermissionError("Path traversal attempt detected")
        return target_path


storage = LocalStorage()
