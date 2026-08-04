from __future__ import annotations

import pytest

from modules.common import derive_run_status
from modules.common.result import OperationResult, StepStatus
from modules.common.run import RunRecorder, RunStatus, run_from_step_reports


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], RunStatus.SUCCEEDED),
        (["succeeded"], RunStatus.SUCCEEDED),
        (["succeeded", "succeeded"], RunStatus.SUCCEEDED),
        (["skipped"], RunStatus.ABANDONED),
        (["abandoned", "unsupported"], RunStatus.ABANDONED),
        (["succeeded", "skipped"], RunStatus.DEGRADED),
        (["succeeded", "abandoned"], RunStatus.DEGRADED),
        (["succeeded", "degraded"], RunStatus.DEGRADED),
        (["failed", "succeeded"], RunStatus.FAILED),
        (["timed_out", "succeeded"], RunStatus.TIMED_OUT),
        (["running", "succeeded"], RunStatus.RUNNING),
    ],
)
def test_derive_run_status(statuses: list[str], expected: str) -> None:
    assert derive_run_status(statuses) == expected


def test_recorder_with_only_skipped_steps_is_abandoned() -> None:
    with RunRecorder(run_type="test") as recorder:
        with recorder.step("cache") as step:
            step.skip(reason="cache disabled")
        with recorder.step("optional") as step:
            step.skip(reason="not requested")

    assert recorder.result.status == RunStatus.ABANDONED
    assert recorder.result.success is False
    assert recorder.result.metadata["terminal_reason"].startswith("all steps")


def test_recorder_with_success_and_skipped_step_is_degraded() -> None:
    with RunRecorder(run_type="test") as recorder:
        with recorder.step("execute") as step:
            step.succeed()
        with recorder.step("verify") as step:
            step.skip(reason="not requested")

    assert recorder.result.status == RunStatus.DEGRADED
    assert recorder.result.success is True
    assert recorder.result.degraded_mode is True


def test_empty_recorder_preserves_backward_compatible_success() -> None:
    with RunRecorder(run_type="test") as recorder:
        pass
    assert recorder.result.status == RunStatus.SUCCEEDED


def test_adapter_with_only_skipped_steps_is_abandoned() -> None:
    run = run_from_step_reports(
        "search",
        {
            "cache": OperationResult.skipped(reason="disabled"),
            "verify": OperationResult.unsupported(reason="not installed"),
        },
    )
    assert run.status == RunStatus.ABANDONED
    assert run.success is False


def test_adapter_mixed_success_and_skip_is_degraded() -> None:
    run = run_from_step_reports(
        "search",
        {
            "execute": OperationResult.success_with(),
            "verify": OperationResult.skipped(reason="not requested"),
        },
    )
    assert run.status == RunStatus.DEGRADED


def test_adapter_degraded_flag_does_not_promote_abandoned_run() -> None:
    run = run_from_step_reports(
        "search",
        {"verify": OperationResult.skipped(reason="not requested")},
        degraded_mode=True,
    )
    assert run.status == RunStatus.ABANDONED
