"""
Context Detector — detect context signals for current session (v1.5 P0)

Signal dimensions:
- file_extension: .py / .ts / .hs / .java / .cpp
- file_directory: markconfig/ / _data/memory/ / modules/ / .opencode/
- session_length: short / medium / long
- keywords: 修复 / 审查 / 搜索 / 重构 / 设计
- test_files: bool — whether any test file is in working set (Phase 1)
- spec_docs: bool — whether any spec/design doc is in working set (Phase 1)
"""
import re
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# File extension → language mapping
EXT_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".hs": "haskell",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
}

# Keyword → task type mapping (used by classifier)
KEYWORDS_MAP = {
    "coding": ["实现", "修复", "添加", "implement", "fix", "add", "refactor"],
    "review": ["审查", "review", "PR", "diff"],
    "research": ["搜索", "调研", "research", "compare", "对比", "查"],
    "debugging": ["bug", "崩溃", "异常", "error", "crash", "fail"],
    "planning": ["设计", "规划", "plan", "architecture", "架构"],
    "writing": ["文档", "报告", "write", "document"],
    "automation": ["浏览器", "browser", "自动化", "automate"],
    "delivery": ["完成", "交付", "验收", "deliver", "done", "finish"],
}

# Phase 1: Test file patterns (triggers test-quality context)
# Order matters: check .test.ts/.spec.ts before .ts to avoid false negatives
TEST_FILE_PATTERNS = [
    re.compile(r'.+\.test\.ts$', re.IGNORECASE),
    re.compile(r'.+\.test\.tsx$', re.IGNORECASE),
    re.compile(r'.+\.spec\.ts$', re.IGNORECASE),
    re.compile(r'.+\.spec\.tsx$', re.IGNORECASE),
    re.compile(r'.+\.test\.js$', re.IGNORECASE),
    re.compile(r'.+\.spec\.js$', re.IGNORECASE),
    re.compile(r'^test_.*\.py$', re.IGNORECASE),
    re.compile(r'.*_test\.py$', re.IGNORECASE),
    re.compile(r'^test_.*\.go$', re.IGNORECASE),  # Go convention
    re.compile(r'.*_test\.go$', re.IGNORECASE),
    re.compile(r'^Test.*\.java$', re.IGNORECASE),  # JUnit convention
]

# Phase 1: Spec / design doc patterns (triggers naming-contract context)
# Matches: *.spec.md, *design*.md, *SPEC*.md, docs/spec/**, specs/**
SPEC_DOC_PATTERNS = [
    re.compile(r'.+\.spec\.md$', re.IGNORECASE),
    re.compile(r'.+\.specification\.md$', re.IGNORECASE),
    re.compile(r'.*design.*\.md$', re.IGNORECASE),
    re.compile(r'.*spec.*\.md$', re.IGNORECASE),
    re.compile(r'.*requirement.*\.md$', re.IGNORECASE),
    re.compile(r'.*prd.*\.md$', re.IGNORECASE),
    # Path-based patterns (checked via Path.parts in detect_spec_doc)
]

SPEC_DOC_DIR_HINTS = {"specs", "spec", "docs", "design"}

# Phase 3D: UI file patterns (triggers ui-components context)
UI_FILE_PATTERNS = [
    re.compile(r'.*[/\\]src[/\\]ui[/\\].*\.tsx?$', re.IGNORECASE),
    re.compile(r'.*[/\\]src[/\\]components[/\\].*\.tsx?$', re.IGNORECASE),
    re.compile(r'.*[/\\]src[/\\]panels[/\\].*\.tsx?$', re.IGNORECASE),
]


def detect_file_extension(file_path: str) -> Optional[str]:
    """Detect programming language from file path extension."""
    ext = Path(file_path).suffix.lower()
    return EXT_MAP.get(ext)


def detect_file_directory(file_path: str) -> Optional[str]:
    """Detect directory context from file path."""
    abs_path = Path(file_path).resolve()
    try:
        rel = abs_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return None

    parts = rel.parts
    if not parts:
        return None

    top = parts[0]
    if top == "markconfig":
        return "secrets"
    if top == "_data" and len(parts) > 1 and parts[1] == "memory":
        return "memory"
    if top == "modules":
        return "modules"
    if top == ".opencode":
        return "opencode"
    if top == "_runtime":
        return "runtime"
    return None


def detect_session_length(turn_count: int) -> str:
    """Classify session length by turn count."""
    if turn_count < 10:
        return "short"
    if turn_count < 50:
        return "medium"
    return "long"


def detect_keywords(message: str) -> list:
    """Detect task keywords from user message."""
    detected = []
    msg_lower = message.lower()
    for task, kws in KEYWORDS_MAP.items():
        for kw in kws:
            if kw.lower() in msg_lower:
                detected.append(task)
                break
    return detected


def detect_test_file(file_path: str) -> bool:
    """Phase 1: Detect if a file is a test file by name pattern.

    Matches common test file conventions across languages:
    - TypeScript/JavaScript: *.test.ts, *.spec.ts, *.test.tsx, *.spec.tsx
    - Python: test_*.py, *_test.py
    - Go: test_*.go, *_test.go
    - Java: Test*.java (JUnit convention)

    Args:
        file_path: path to check (basename is what matters)

    Returns:
        True if file_path matches any test file pattern
    """
    if not file_path:
        return False
    basename = Path(file_path).name
    for pattern in TEST_FILE_PATTERNS:
        if pattern.match(basename):
            return True
    return False


def detect_ui_file(file_path: str) -> bool:
    """Phase 3D: Detect if a file is in the UI layer.

    Matches files under src/ui/, src/components/, or src/panels/ directories.
    Triggers ui-components context (priority 96) for UI architecture enforcement.

    Args:
        file_path: path to check

    Returns:
        True if file_path matches UI file patterns
    """
    if not file_path:
        return False
    # Normalize path separators for cross-platform matching
    normalized = file_path.replace('\\', '/')
    for pattern in UI_FILE_PATTERNS:
        if pattern.match(normalized):
            return True
    return False


def detect_spec_doc(file_path: str) -> bool:
    """Phase 1: Detect if a file is a spec / design / requirement doc.

    Triggers naming-contract context so the agent treats names in the doc
    as contracts rather than suggestions.

    Matches:
    - File name patterns: *.spec.md, *design*.md, *spec*.md, *requirement*.md, *prd*.md
    - Path patterns: any file under specs/ / spec/ / docs/ / design/ directories
      (only when the file is a .md)

    Args:
        file_path: path to check

    Returns:
        True if file_path matches spec doc patterns
    """
    if not file_path:
        return False
    p = Path(file_path)
    name = p.name
    suffix = p.suffix.lower()

    # Only markdown files are docs
    if suffix != ".md":
        return False

    # Check filename patterns
    for pattern in SPEC_DOC_PATTERNS:
        if pattern.match(name):
            return True

    # Check directory hints (specs/, spec/, docs/, design/)
    try:
        abs_path = p.resolve()
        rel = abs_path.relative_to(PROJECT_ROOT)
        parts_lower = {part.lower() for part in rel.parts[:-1]}  # exclude filename
        if parts_lower & SPEC_DOC_DIR_HINTS:
            return True
    except (ValueError, OSError):
        # Outside project or path resolution issue — fall back to raw parts
        parts_lower = {part.lower() for part in p.parts[:-1]}
        if parts_lower & SPEC_DOC_DIR_HINTS:
            return True

    return False


def detect_context_signals(
    message: str,
    file_paths: Optional[list] = None,
    turn_count: int = 0,
) -> dict:
    """
    Detect all context signals.

    Returns:
        {
            "languages": list[str],      # detected programming languages
            "directories": list[str],    # detected directory contexts
            "session_length": str,       # short / medium / long
            "task_hints": list[str],     # keyword-matched task types
            "has_test_files": bool,      # Phase 1: any test file in working set
            "has_spec_docs": bool,       # Phase 1: any spec/design doc in working set
            "has_ui_files": bool,        # Phase 3D: any UI file in working set
        }
    """
    languages = set()
    directories = set()
    has_test_files = False
    has_spec_docs = False
    has_ui_files = False

    if file_paths:
        for fp in file_paths:
            lang = detect_file_extension(fp)
            if lang:
                languages.add(lang)
            dir_ctx = detect_file_directory(fp)
            if dir_ctx:
                directories.add(dir_ctx)
            if detect_test_file(fp):
                has_test_files = True
            if detect_spec_doc(fp):
                has_spec_docs = True
            if detect_ui_file(fp):
                has_ui_files = True

    return {
        "languages": sorted(languages),
        "directories": sorted(directories),
        "session_length": detect_session_length(turn_count),
        "task_hints": detect_keywords(message),
        "has_test_files": has_test_files,
        "has_spec_docs": has_spec_docs,
        "has_ui_files": has_ui_files,
    }


def suggest_contexts(signals: dict) -> list:
    """Suggest contexts to load based on detected signals.

    Phase 1 additions:
    - `anti-patterns`: loaded when any code language is detected (always-on for code tasks)
    - `naming-contract`: loaded when spec docs are present OR when code files are touched
      (code files imply names exist that should follow doc contracts)
    - `test-quality`: loaded when test files are detected

    Phase 2 additions:
    - `flash-role`: always loaded (priority 99, constitutional layer for
      multi-model collaboration policy)

    Phase 3 additions:
    - `ui-components`: loaded when UI files are detected (priority 96,
      enforces component-based architecture)
    """
    contexts = []
    has_code_language = bool(signals.get("languages"))

    # Phase 2: flash-role context — always loaded (constitutional layer)
    # (priority 99, highest priority, documents Flash role guard policy)
    contexts.append("flash-role")

    # Phase 3D: ui-components — loaded when UI files are touched
    # (priority 96, between flash-role and anti-patterns)
    if signals.get("has_ui_files"):
        contexts.append("ui-components")

    # Phase 1: anti-patterns context — always loaded when code is present
    # (priority 95, highest of the 3 new contexts)
    if has_code_language:
        contexts.append("anti-patterns")

    # Language-specific contexts
    for lang in signals["languages"]:
        contexts.append(lang)

    # Phase 1: naming-contract — when spec docs present OR code files touched
    # (priority 92, loaded alongside language context)
    if signals.get("has_spec_docs") or has_code_language:
        contexts.append("naming-contract")

    for d in signals["directories"]:
        if d in ("secrets", "memory"):
            contexts.append(d)
    if signals["session_length"] == "long":
        contexts.append("long-session")

    # Phase 1: test-quality — only when test files are in working set
    # (priority 88, loaded last so it's near examples)
    if signals.get("has_test_files"):
        contexts.append("test-quality")

    return contexts


def suggest_task(signals: dict) -> Optional[str]:
    """Suggest task type based on detected signals (first match)."""
    if signals["task_hints"]:
        return signals["task_hints"][0]
    return None
