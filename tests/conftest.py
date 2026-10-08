"""Pytest fixtures for database, storage, and test client."""

import pytest
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, get_db
from app.main import app


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated temporary directory for file storage during testing."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    return storage_dir


@pytest.fixture
def db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Provide a fresh, isolated SQLite database per test and patch SessionLocal."""
    db_file = tmp_path / "test.db"
    test_db_url = f"sqlite:///{db_file.as_posix()}"
    test_engine = create_engine(
        test_db_url,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(test_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.services.processor.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(settings, "DATABASE_URL", test_db_url)

    Base.metadata.create_all(bind=test_engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(db_session: Session, tmp_storage: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide a TestClient with overridden DB dependency and eager execution."""
    monkeypatch.setattr(settings, "EAGER", True)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
