"""Background worker pool and job submission management."""

from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Optional

from app.config import settings
from app.services.processor import process_job

logger = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None


def get_executor() -> ThreadPoolExecutor:
    """Retrieve or initialize the singleton ThreadPoolExecutor."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.WORKERS,
            thread_name_prefix="cert-worker",
        )
    return _executor


def submit(job_id: str) -> None:
    """
    Submit a job for background processing.
    If EAGER mode is enabled (e.g. in test suites), executes synchronously inline.
    """
    if settings.EAGER:
        logger.info("Executing job %s eagerly inline", job_id)
        process_job(job_id)
    else:
        logger.info("Submitting job %s to worker pool", job_id)
        executor = get_executor()
        executor.submit(process_job, job_id)


def shutdown_workers(wait: bool = True) -> None:
    """Gracefully shut down the background worker pool."""
    global _executor
    if _executor is not None:
        logger.info("Shutting down worker pool (wait=%s)", wait)
        _executor.shutdown(wait=wait, cancel_futures=False)
        _executor = None
