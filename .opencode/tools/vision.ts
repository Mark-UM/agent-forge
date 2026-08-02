import { tool } from "@opencode-ai/plugin"
import path from "path"

const PYTHON = "python"

export default tool({
  description: "Recognize image or PDF content using SiliconFlow Qwen3-VL model. ALWAYS use this tool instead of Read for any image/PDF file. Supports multiple images.",
  args: {
    files: tool.schema.array(tool.schema.string()).describe("Array of image/PDF file paths to recognize"),
    prompt: tool.schema.string().optional().describe("Optional custom prompt for recognition. Defaults to a detailed content description request"),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "modules/vision/recognize.py")
    const prompt = args.prompt || "Describe the content of these images in detail."

    // Bun's tagged template passes each interpolated value as an argument.
    const result = await Bun.$`${PYTHON} ${script} ${[...args.files, prompt]}`.text()
    return result.trim()
  },
})
