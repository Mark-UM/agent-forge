from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def wait_for_owned_scheduler_workers(request):
    """Keep temporary scheduler databases alive until owned workers finish.

    Dynamically requesting ``temp_db`` when the test declares it establishes a
    fixture dependency: this wait fixture tears down first, while the temporary
    directory and monkeypatched database path are still valid. Tests without a
    temp database do not create one unnecessarily.
    """

    if "temp_db" in request.fixturenames:
        request.getfixturevalue("temp_db")

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
