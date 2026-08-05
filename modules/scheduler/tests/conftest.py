from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def wait_for_owned_scheduler_workers():
    """Keep temporary scheduler databases alive until owned workers finish.

    HTTP handler tests intentionally exercise the asynchronous manual-run path.
    Without this fixture the test body can finish immediately after receiving
    ``202 queued`` and pytest tears down the monkeypatched SQLite path while the
    named worker is still starting. The resulting exception belongs to fixture
    lifetime, not the scheduler operation, and can leak into a later test.
    """

    yield
    deadline = time.monotonic() + 5.0
    while True:
        workers = [
            thread
            for thread in threading.enumerate()
            if thread is not threading.current_thread()
            and thread.name.startswith("job-run-")
            and thread.is_alive()
        ]
        if not workers:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(
                "scheduler worker did not terminate before fixture teardown: "
                + ", ".join(thread.name for thread in workers)
            )
        for worker in workers:
            worker.join(timeout=min(remaining, 0.25))
