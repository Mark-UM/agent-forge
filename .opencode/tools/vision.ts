import { tool } from "@opencode-ai/plugin"
import path from "path"

const PYTHON = "C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe"

export default tool({
  description: "Recognize image or PDF content using SiliconFlow Qwen3-VL model. ALWAYS use this tool instead of Read for any image/PDF file. Supports multiple images.",
  args: {
    files: tool.schema.array(tool.schema.string()).describe("Array of image/PDF file paths to recognize"),
    prompt: tool.schema.string().optional().describe("Optional custom prompt for recognition. Defaults to '请详细描述这些图片的内容'"),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "modules/vision/recognize.py")
    const prompt = args.prompt || "请详细描述这些图片的内容"

    // 使用数组传参避免 shell 注入，每个参数独立传递
    const result = await Bun.$`${PYTHON} ${script} ${[...args.files, prompt]}`.text()
    return result.trim()
  },
})
