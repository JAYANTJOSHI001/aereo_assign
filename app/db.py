"""Database session, engine configuration, and base declarative model."""

import os
from collections.abc import Generator
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import settings

# Ensure SQLite directory exists if local file path is configured
if settings.DATABASE_URL.startswith("sqlite"):
    db_path_str = settings.DATABASE_URL.replace("sqlite:///", "")
    if db_path_str and not db_path_str.startswith(":memory:"):
        db_path = Path(db_path_str).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        """Enable Write-Ahead Logging and set busy timeout for concurrent access."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
