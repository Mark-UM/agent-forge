"""Experimental, fixed-Provider Kernel -> MyHarness JSONL vertical slice.

Run on Windows after building the exact MyHarness candidate and copying
`.github/f2/faux.ts` to `<source>/f2-faux.ts`. This is a disposable demo,
not the production Executor or the F2 isolation acceptance matrix.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.kernel import (  # noqa: E402
    AgentResult,
    AgentRole,
    AgentSpec,
    BudgetLimit,
    CancellationToken,
    DeterministicCoordinator,
    EXECUTOR_PROTOCOL_VERSION,
    ExecutorCapabilities,
    ExecutorEvent,
    ExecutorHealth,
    ExecutorRequest,
    ExecutorResult,
    KernelExecutionEngine,
    PermissionRequest,
    PermissionScope,
    TaskRepository,
    TaskStatus,
)

SOURCE_COMMIT = "5be723be1b5c34cae2abe6fea5718f0407f91760"
SOURCE_TREE = "42e4195294ae1825c9025ccfe0a48d256814ce5d"
CAPABILITY = "text.generate"
FIXED_PROMPT = "Return the fixed fixture."
FIXED_OUTPUT = "F2_FIXTURE_SUCCESS"


def verify_source(source: Path) -> None:
    for ref, expected in (("HEAD", SOURCE_COMMIT), ("HEAD^{tree}", SOURCE_TREE)):
        actual = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", ref],
            text=True, timeout=5,
        ).strip()
        if actual != expected:
            raise RuntimeError(f"MyHarness {ref} mismatch")
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "f2-faux.ts"):
        if not (source / name).is_file():
            raise RuntimeError(f"MyHarness demo requires {name}")
    if not (source / "packages" / "coding-agent" / "dist" / "cli.js").is_file():
        raise RuntimeError("MyHarness CLI has not been built")


class DemoMyHarnessExecutor:
    """Only maps one no-tool fixed-Provider command to the F1 seam."""

    def __init__(self, *, source: Path, node: Path, scratch: Path) -> None:
        self.source = source
        self.node = node
        self.scratch = scratch

    def health(self) -> ExecutorHealth:
        return ExecutorHealth(self.node.is_file() and self.source.is_dir())

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(EXECUTOR_PROTOCOL_VERSION, (CAPABILITY,))

    def execute(
        self, request: ExecutorRequest, cancellation: CancellationToken
    ) -> ExecutorResult:
        cancellation.raise_if_cancelled()
        command = request.command
        if (
            command.required_capabilities != (CAPABILITY,)
            or command.tool_name is not None
            or command.permission_request != PermissionRequest()
            or command.normalized_input != {"text": FIXED_PROMPT}
            or command.attempt != 1
        ):
            return ExecutorResult(
                EXECUTOR_PROTOCOL_VERSION,
                request.request_id,
                AgentResult.failed("demo accepts only the fixed no-tool request", code="demo_request_denied"),
            )
        if command.remaining_budget.wall_clock_seconds < 40:
            return ExecutorResult(
                EXECUTOR_PROTOCOL_VERSION,
                request.request_id,
                AgentResult.failed("demo requires 40 seconds of remaining budget", code="demo_budget_denied"),
            )

        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "ComSpec", "PATH", "PATHEXT")
            if key in os.environ
        }
        environment.update(
            F2_SOURCE=str(self.source),
            F2_SCRATCH=str(self.scratch),
            F2_PROMPT=FIXED_PROMPT,
        )
        launcher = ROOT / ".github" / "f2" / "startup.mjs"
        try:
            completed = subprocess.run(
                [str(self.node), str(launcher)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result = AgentResult.timed_out("fixed MyHarness demo timed out")
        else:
            cancellation.raise_if_cancelled()
            if completed.returncode != 0 or len(completed.stdout) > 8192:
                result = AgentResult.failed("fixed MyHarness demo failed", code="demo_cli_failed")
            else:
                try:
                    data = json.loads(completed.stdout)
                    valid = (
                        data.get("sourceCommit") == SOURCE_COMMIT
                        and data.get("assistantText") == FIXED_OUTPUT
                        and data.get("fixedProviderReached") is True
                        and data.get("toolsExecuted") is False
                    )
                except (ValueError, AttributeError):
                    valid = False
                result = (
                    AgentResult.succeeded({"result": FIXED_OUTPUT})
                    if valid
                    else AgentResult.failed("fixed MyHarness demo returned invalid output", code="demo_output_invalid")
                )
        return ExecutorResult(
            EXECUTOR_PROTOCOL_VERSION,
            request.request_id,
            result,
            events=(
                ExecutorEvent(request.request_id, 1, "executor.started"),
                ExecutorEvent(request.request_id, 2, "executor.finished"),
            ),
        )


def spec(agent_id: str, role: AgentRole) -> AgentSpec:
    capabilities = (CAPABILITY,) if role is AgentRole.WORKER else ()
    return AgentSpec(
        agent_id=agent_id,
        role=role,
        capabilities=capabilities,
        allowed_tools=(),
        model_policy={"gateway_route": "deterministic_stub"},
        permission_scope=PermissionScope(workspace_roots=("workspace",)),
        budget_limits=BudgetLimit(),
        input_contract={"type": "object", "required": ["text"]},
        output_contract={"type": "object", "required": ["result"]},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--node", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    node = args.node.resolve(strict=True)
    verify_source(source)

    with TemporaryDirectory(prefix="agentforge-myharness-f2-") as directory:
        scratch = Path(directory)
        repository = TaskRepository(scratch / "kernel.db")
        task, created = repository.create_task(
            objective="Return the fixed MyHarness fixture through Kernel",
            normalized_input={"text": FIXED_PROMPT},
            idempotency_key="f2-fixed-provider-demo",
            required_capabilities=(CAPABILITY,),
            budget=BudgetLimit(wall_clock_seconds=60),
        )
        if not created:
            raise RuntimeError("demo Task was not created")
        coordinator = spec("coordinator.demo", AgentRole.COORDINATOR)
        worker = spec("worker.myharness", AgentRole.WORKER)
        executor = DemoMyHarnessExecutor(source=source, node=node, scratch=scratch)
        engine = KernelExecutionEngine(
            repository,
            DeterministicCoordinator(coordinator, (worker,)),
            {worker.agent_id: executor},
        )
        outcome = engine.execute_task(task.task_id, prefer_direct=False)
        if outcome.task.status is not TaskStatus.SUCCEEDED or not outcome.agent_result:
            raise RuntimeError(f"Kernel/MyHarness demo failed: {outcome.task.status.value}")
        print(json.dumps({
            "taskStatus": outcome.task.status.value,
            "executorProtocol": EXECUTOR_PROTOCOL_VERSION,
            "sourceCommit": SOURCE_COMMIT,
            "result": outcome.agent_result.output["result"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
