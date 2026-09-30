import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { runJsonlProbe } from "./jsonl-probe.mjs";

const root = process.env.GITHUB_WORKSPACE ?? process.cwd();
const scratch = process.env.F2_SCRATCH ?? path.join(process.env.RUNNER_TEMP ?? os.tmpdir(), "f2-startup");
const expectedWorkspace = path.join(scratch, "workspace");
mkdirSync(expectedWorkspace, { recursive: true });
const workspace = process.env.F2_WORKSPACE ?? expectedWorkspace;
const expectedIdentity = statSync(expectedWorkspace, { bigint: true });
const boundIdentity = statSync(workspace, { bigint: true });
if (expectedIdentity.dev !== boundIdentity.dev || expectedIdentity.ino !== boundIdentity.ino) {
  throw new Error("F2 Workspace binding mismatch");
}
writeFileSync(path.join(workspace, "fixture.txt"), "disposable startup fixture\n");
mkdirSync(path.join(scratch, "AppData", "Roaming"), { recursive: true });
mkdirSync(path.join(scratch, "AppData", "Local"), { recursive: true });

const source = process.env.F2_SOURCE ?? path.join(root, "myharness");
for (const [ref, expected] of [
  ["HEAD", "5be723be1b5c34cae2abe6fea5718f0407f91760"],
  ["HEAD^{tree}", "42e4195294ae1825c9025ccfe0a48d256814ce5d"],
]) {
  const result = spawnSync("git", ["-C", source, "rev-parse", ref], { encoding: "utf8", windowsHide: true, timeout: 5000 });
  if (result.status !== 0 || result.stdout.trim() !== expected) throw new Error(`MyHarness ${ref} mismatch`);
}
for (const name of ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "f2-faux.ts"]) {
  if (!existsSync(path.join(source, name))) throw new Error(`MyHarness ${name} missing`);
}
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

const result = await runJsonlProbe({
  executable: process.execPath,
  args,
  cwd: workspace,
  env,
  expectedText: "F2_FIXTURE_SUCCESS",
});
console.log(JSON.stringify({
  sourceCommit: "5be723be1b5c34cae2abe6fea5718f0407f91760",
  attemptId: process.env.F2_ATTEMPT_ID ?? null,
  ...result,
  fixedProviderReached: true,
  runtime: process.version,
}));
