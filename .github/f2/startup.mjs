import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const root = process.env.GITHUB_WORKSPACE ?? process.cwd();
const scratch = process.env.F2_SCRATCH ?? path.join(process.env.RUNNER_TEMP ?? os.tmpdir(), "f2-startup");
const workspace = path.join(scratch, "workspace");
mkdirSync(workspace, { recursive: true });
writeFileSync(path.join(workspace, "fixture.txt"), "disposable startup fixture\n");
mkdirSync(path.join(scratch, "AppData", "Roaming"), { recursive: true });
mkdirSync(path.join(scratch, "AppData", "Local"), { recursive: true });

const source = process.env.F2_SOURCE ?? path.join(root, "myharness");
const cli = path.join(source, "packages", "coding-agent", "dist", "cli.js");
const extension = path.join(source, "f2-faux.ts");
const systemRoot = process.env.SystemRoot;
const env = {
  SystemRoot: systemRoot,
  WINDIR: systemRoot,
  ComSpec: path.join(systemRoot, "System32", "cmd.exe"),
  PATH: [path.dirname(process.execPath), path.join(systemRoot, "System32"), systemRoot].join(path.delimiter),
  PATHEXT: ".COM;.EXE;.BAT;.CMD",
  HOME: scratch,
  USERPROFILE: scratch,
  APPDATA: path.join(scratch, "AppData", "Roaming"),
  LOCALAPPDATA: path.join(scratch, "AppData", "Local"),
  TEMP: scratch,
  TMP: scratch,
  CI: "true",
};
const args = [
  cli, "--mode", "json", "--no-session", "--no-tools", "--no-extensions",
  "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
  "--offline", "--approve", "--extension", extension,
  "--provider", "f2-faux", "--model", "faux-1",
  process.env.F2_PROMPT ?? "Return the fixed fixture.",
];

const limit = 131072;
let bytes = 0;
let stdout = "";
let stderr = "";
let failure = null;
const child = spawn(process.execPath, args, { cwd: workspace, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
const stopTree = () => {
  if (child.pid) spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, timeout: 5000 });
};
const timer = setTimeout(() => { failure = "CLI exceeded 30 seconds"; stopTree(); }, 30000);
for (const [stream, append] of [
  [child.stdout, (text) => { stdout += text; }],
  [child.stderr, (text) => { stderr += text; }],
]) {
  stream.on("data", (chunk) => {
    if (failure) return;
    bytes += chunk.length;
    if (bytes > limit) {
      failure = "CLI output exceeded 128 KiB";
      stopTree();
      return;
    }
    append(chunk.toString("utf8"));
  });
}

let exitCode;
try {
  exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  });
} finally { clearTimeout(timer); }
if (failure) throw new Error(failure);
if (exitCode !== 0) throw new Error(`CLI exit ${exitCode}: ${stderr.slice(0, 2048)}`);

const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
const events = lines.map((line) => JSON.parse(line));
if (events.some((event) => !event || typeof event.type !== "string")) {
  throw new Error("JSONL event has no string type");
}
if (events.length < 2 || events[0].type !== "session" || events[0].version !== 3) {
  throw new Error("Missing JSONL v3 session header");
}
if (!events.some((event) => event.type === "agent_end")) {
  throw new Error("Missing terminal agent_end event");
}
if (events.some((event) => event.type?.startsWith("tool_execution_"))) {
  throw new Error("Unexpected tool execution event");
}
const assistant = events.filter((event) => event.type === "message_end" && event.message?.role === "assistant").at(-1);
const assistantText = assistant?.message?.content?.filter((block) => block.type === "text")
  .map((block) => block.text).join("");
if (assistantText !== "F2_FIXTURE_SUCCESS") throw new Error("Fixed Provider response missing from final assistant message");
console.log(JSON.stringify({
  sourceCommit: "5be723be1b5c34cae2abe6fea5718f0407f91760",
  jsonlEvents: events.length,
  outputBytes: bytes,
  eventTypes: [...new Set(events.map((event) => event.type))],
  fixedProviderReached: true,
  assistantText,
  toolsExecuted: false,
  runtime: process.version,
}));
