import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmdirSync, unlinkSync } from "node:fs";
import { test } from "node:test";
import path from "node:path";
import { runJsonlProbe } from "./jsonl-probe.mjs";

const session = { type: "session", version: 3 };
const assistant = {
  type: "message_end",
  message: { role: "assistant", content: [{ type: "text", text: "F2_FIXTURE_SUCCESS" }] },
};
const terminal = { type: "agent_end" };

function script(events) {
  return `for (const event of ${JSON.stringify(events)}) console.log(JSON.stringify(event));`;
}

function probe(program, options = {}) {
  return runJsonlProbe({
    executable: process.execPath,
    args: ["-e", program],
    cwd: process.cwd(),
    env: { SystemRoot: process.env.SystemRoot, PATH: process.env.PATH },
    expectedText: "F2_FIXTURE_SUCCESS",
    timeoutMs: 2000,
    outputLimit: 4096,
    ...options,
  });
}

test("accepts a bounded, no-tool JSONL v3 result", async () => {
  const result = await probe(script([
    session, assistant, terminal,
    { type: "run_state_changed" }, { type: "run_state_changed" }, { type: "agent_settled" },
  ]));
  assert.equal(result.assistantText, "F2_FIXTURE_SUCCESS");
  assert.equal(result.toolsExecuted, false);
  assert.equal(result.jsonlEvents, 6);
});

test("rejects malformed and unknown JSONL events", async () => {
  await assert.rejects(probe("process.stdout.write('{bad\\n')"), /invalid JSONL/);
  await assert.rejects(probe(script([session, { type: "unexpected" }, assistant, terminal])), /unknown event/);
});

test("rejects missing or reordered terminal evidence", async () => {
  await assert.rejects(probe(script([assistant, terminal])), /session header/);
  await assert.rejects(probe(script([session, terminal, assistant])), /terminal order/);
  await assert.rejects(probe(script([session, assistant, terminal, terminal])), /terminal order/);
  await assert.rejects(
    probe(script([session, assistant, terminal, { type: "turn_start" }])),
    /terminal order/,
  );
  await assert.rejects(
    probe(script([session, assistant, terminal, { type: "agent_settled" }, { type: "run_state_changed" }])),
    /terminal order/,
  );
  await assert.rejects(
    probe(script([
      session, assistant, terminal,
      { type: "run_state_changed" }, { type: "run_state_changed" }, { type: "run_state_changed" },
    ])),
    /terminal order/,
  );
});

test("rejects tool execution and a missing fixed answer", async () => {
  await assert.rejects(
    probe(script([session, { type: "tool_execution_start" }, assistant, terminal])),
    /tool execution/,
  );
  await assert.rejects(probe(script([session, terminal, { type: "agent_settled" }])), /fixed Provider response/);
});

test("maps a child crash to failure without emitting its stderr", async () => {
  await assert.rejects(
    probe("console.error('PRIVATE_FIXTURE_SENTINEL'); process.exit(7)"),
    (error) => error.message.includes("CLI exit 7") && !error.message.includes("PRIVATE_FIXTURE_SENTINEL"),
  );
});

test("rejects a missing executable and a missing agent end", async () => {
  await assert.rejects(probe(script([session, assistant])), /terminal order/);
  await assert.rejects(
    runJsonlProbe({
      executable: path.join(process.cwd(), "missing-f2-cli.exe"),
      args: [], cwd: process.cwd(), env: {},
      expectedText: "F2_FIXTURE_SUCCESS", timeoutMs: 1000,
    }),
    /CLI launch failed: ENOENT/,
  );
});

test("bounds combined output while it streams", async () => {
  await assert.rejects(
    probe("process.stderr.write('x'.repeat(8192)); setInterval(() => {}, 1000)", { outputLimit: 512 }),
    /output exceeded/,
  );
});

test("times out and cancels a stalled child", async () => {
  const program = "setInterval(() => {}, 1000)";
  await assert.rejects(probe(program, { timeoutMs: 200 }), /exceeded 200 ms/);
  const controller = new AbortController();
  const pending = probe(program, { signal: controller.signal });
  setTimeout(() => controller.abort(), 100);
  await assert.rejects(pending, /cancelled/);
});

test("timeout removes a descendant process on Windows", {
  skip: process.platform !== "win32",
}, async () => {
  const directory = mkdtempSync(path.join(process.cwd(), ".f2-tree-"));
  const pidFile = path.join(directory, "descendant.pid");
  let pid;
  try {
    const program = `
      const { spawn } = require("node:child_process");
      const { writeFileSync } = require("node:fs");
      const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"],
        { stdio: "ignore", windowsHide: true });
      writeFileSync(${JSON.stringify(pidFile)}, String(child.pid));
      setInterval(() => {}, 1000);
    `;
    await assert.rejects(probe(program, { timeoutMs: 500 }), /exceeded 500 ms/);
    pid = Number(readFileSync(pidFile, "utf8"));
    assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
  } finally {
    if (pid) {
      try {
        process.kill(pid, 0);
        spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
      } catch (error) {
        if (error.code !== "ESRCH") throw error;
      }
    }
    if (existsSync(pidFile)) unlinkSync(pidFile);
    rmdirSync(directory);
  }
});
