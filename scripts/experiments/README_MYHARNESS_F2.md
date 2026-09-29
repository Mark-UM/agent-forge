# Agent Forge → MyHarness fixed-Provider demo

This is the first runnable Kernel → Executor → MyHarness vertical slice. It
accepts one fixed no-tool Task, launches the exact MyHarness candidate in JSON
mode with a local fake Provider, and returns the final assistant message to a
Kernel-owned Task. The successful Windows runner evidence is recorded in
[F2 Entry Gates](../../_docs/roadmap/2.0_F2_ENTRY_GATES.md).

## Run in GitHub Actions

On `main`, select **F2 Windows startup probe
(experimental)** and **Run workflow**. The job uses a disposable Windows VM,
checks out the exact MyHarness commit, builds from its lock without lifecycle
scripts, runs bounded JSONL process failure tests, then runs both the JSONL
startup probe and the Kernel vertical slice.
No Provider credential or private data is needed.

## Run in a disposable local Windows checkout

Use Python 3.11 and Node 22.22.1. From the Agent Forge repository root:

```powershell
node --test .github/f2/jsonl-probe.test.mjs
```

Then build the exact MyHarness checkout and run the vertical slice:

```powershell
git clone https://github.com/Mark-UM/MyHarness.git myharness
git -C myharness checkout 5be723be1b5c34cae2abe6fea5718f0407f91760
Push-Location myharness
npm ci --ignore-scripts --no-audit --no-fund
npm run build
Pop-Location
Copy-Item .github/f2/faux.ts myharness/f2-faux.ts
python -m pip install APScheduler==3.11.3 tzdata==2026.3
python scripts/experiments/kernel_myharness_vertical.py --source myharness --node (Get-Command node).Source
```

The final line should print a JSON object containing `taskStatus: succeeded`
and `result: F2_FIXTURE_SUCCESS`. The demo verifies the source commit and tree
before execution. It uses disposable input and does not need a real model.

This demo does not enforce a read-only Workspace or block network egress. Its
synthetic process tests cover only part of the F2 failure matrix. Run it only
in a disposable checkout without private data or production credentials. It is
not enabled as a production
Executor; F3 remains behind the F2 gates.
