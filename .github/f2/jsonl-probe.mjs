import { spawn, spawnSync } from "node:child_process";

const allowedEvents = new Set([
  "session", "run_state_changed", "agent_start", "turn_start", "message_start",
  "message_update", "message_end", "provider_response", "turn_end", "agent_end",
  "agent_settled",
]);

function parseEvents(output, expectedText) {
  let events;
  try {
    events = output.trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  } catch (error) {
    throw new Error("invalid JSONL from CLI", { cause: error });
  }
  if (events.length < 2 || events[0]?.type !== "session" || events[0]?.version !== 3) {
    throw new Error("missing JSONL v3 session header");
  }
  if (events.length > 256) throw new Error("JSONL event count exceeded 256");
  for (const event of events) {
    if (typeof event?.type !== "string") throw new Error("invalid JSONL event type");
    if (event.type.startsWith("tool_execution_")) throw new Error("unexpected tool execution event");
    if (!allowedEvents.has(event.type)) throw new Error("unknown event type");
  }
  const end = events.findIndex((event) => event.type === "agent_end");
  const tail = events.slice(end + 1);
  if (end < 0 || tail.length < 1 || tail.length > 3
    || tail.at(-1).type !== "agent_settled"
    || tail.slice(0, -1).some((event) => event.type !== "run_state_changed")) {
    throw new Error("invalid JSONL terminal order");
  }
  const assistant = events.slice(0, end).filter(
    (event) => event.type === "message_end" && event.message?.role === "assistant",
  ).at(-1);
  const blocks = assistant?.message?.content;
  const assistantText = Array.isArray(blocks)
    ? blocks.filter((block) => block?.type === "text" && typeof block.text === "string")
      .map((block) => block.text).join("")
    : undefined;
  if (assistantText !== expectedText) throw new Error("fixed Provider response missing from final assistant message");
  return {
    jsonlEvents: events.length,
    eventTypes: [...new Set(events.map((event) => event.type))],
    assistantText,
    toolsExecuted: false,
  };
}

export async function runJsonlProbe({
  executable, args, cwd, env, expectedText, timeoutMs = 30000,
  outputLimit = 131072, signal,
}) {
  if (signal?.aborted) throw new Error("CLI cancelled");
  const stdoutChunks = [];
  let bytes = 0;
  let failure = null;
  const child = spawn(executable, args, {
    cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  const stopTree = () => {
    if (child.pid && process.platform === "win32") {
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true, timeout: 5000, stdio: "ignore",
      });
    }
    if (!child.killed) child.kill();
  };
  const fail = (message) => {
    if (failure) return;
    failure = message;
    stopTree();
  };
  const timer = setTimeout(() => fail(`CLI exceeded ${timeoutMs} ms`), timeoutMs);
  const cancel = () => fail("CLI cancelled");
  signal?.addEventListener("abort", cancel, { once: true });
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => {
      if (failure) return;
      bytes += chunk.length;
      if (bytes > outputLimit) {
        fail(`CLI output exceeded ${outputLimit} bytes`);
        return;
      }
      if (stream === child.stdout) stdoutChunks.push(chunk);
    });
  }
  let exitCode;
  let processError;
  try {
    exitCode = await new Promise((resolve) => {
      child.once("error", (error) => { processError = error; resolve(null); });
      child.once("close", resolve);
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
  if (failure) throw new Error(failure);
  if (processError) throw new Error(`CLI launch failed: ${processError.code ?? "unknown"}`);
  if (exitCode !== 0) throw new Error(`CLI exit ${exitCode}`);
  return {
    ...parseEvents(Buffer.concat(stdoutChunks).toString("utf8"), expectedText),
    outputBytes: bytes,
  };
}
