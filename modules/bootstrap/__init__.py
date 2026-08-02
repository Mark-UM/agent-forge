"""project-bootstrap module (Phase 3A, Direction 1).

Generates the 8 mandatory engineering scaffold files for a TypeScript/Vite project:
- .eslintrc.cjs (zero 'off' rules)
- .prettierrc + .prettierignore
- .husky/pre-commit
- .github/workflows/ci.yml + deploy.yml
- tsconfig.json (strict: true + 8 strict flags)
- vite.config.ts (terser + drop_console + drop_debugger + manualChunks)
- vitest.config.ts (coverage.thresholds ≥85%)

Public API:
    generate_scaffold(project_root: Path, force: bool = False) -> dict
    list_templates() -> list[str]
    validate_scaffold(project_root: Path) -> dict
"""
from pathlib import Path
from .generator import generate_scaffold, list_templates, validate_scaffold

__all__ = ['generate_scaffold', 'list_templates', 'validate_scaffold']
__version__ = '1.0.0'
