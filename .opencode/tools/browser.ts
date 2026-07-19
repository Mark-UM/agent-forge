import { tool } from "@opencode-ai/plugin"

const DAEMON_PORT = 9223
const BASE_URL = `http://127.0.0.1:${DAEMON_PORT}`

async function callBrowser(action: string, body: Record<string, unknown> = {}): Promise<string> {
  const resp = await fetch(`${BASE_URL}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const data = await resp.json()
  return JSON.stringify(data, null, 2)
}

export const navigate = tool({
  description: "Navigate the browser to a URL. Auto-starts the browser daemon if not running.",
  args: {
    url: tool.schema.string().describe("URL to navigate to"),
  },
  async execute(args) {
    return callBrowser("navigate", { url: args.url })
  },
})

export const click = tool({
  description: "Click an element on the page by CSS selector",
  args: {
    selector: tool.schema.string().describe("CSS selector of the element to click"),
  },
  async execute(args) {
    return callBrowser("click", { selector: args.selector })
  },
})

export const type = tool({
  description: "Type text into an input field by CSS selector",
  args: {
    selector: tool.schema.string().describe("CSS selector of the input field"),
    text: tool.schema.string().describe("Text to type"),
  },
  async execute(args) {
    return callBrowser("type", { selector: args.selector, text: args.text })
  },
})

export const screenshot = tool({
  description: "Take a full-page screenshot of the current browser page",
  args: {
    filepath: tool.schema.string().optional().describe("Optional path to save screenshot. Defaults to modules/browser/screenshot.png"),
  },
  async execute(args) {
    return callBrowser("screenshot", { path: args.filepath })
  },
})

export const page_text = tool({
  description: "Get the text content of the current page",
  args: {},
  async execute() {
    return callBrowser("text")
  },
})

export const page_content = tool({
  description: "Get the HTML content of the current page",
  args: {},
  async execute() {
    return callBrowser("content")
  },
})

export const execute_js = tool({
  description: "Execute JavaScript code in the browser page context",
  args: {
    code: tool.schema.string().describe("JavaScript code to execute"),
  },
  async execute(args) {
    return callBrowser("js", { code: args.code })
  },
})

export const browser_status = tool({
  description: "Get the current browser page status (URL, title)",
  args: {},
  async execute() {
    return callBrowser("status")
  },
})

export const scroll = tool({
  description: "Scroll the page to specified coordinates",
  args: {
    x: tool.schema.number().optional().describe("X coordinate, default 0"),
    y: tool.schema.number().optional().describe("Y coordinate, default 0"),
  },
  async execute(args) {
    return callBrowser("scroll", { x: args.x || 0, y: args.y || 0 })
  },
})
