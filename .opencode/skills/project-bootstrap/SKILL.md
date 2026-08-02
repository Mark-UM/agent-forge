---
name: project-bootstrap
description: Generate and statically validate the repository's nine-file TypeScript/Vite engineering scaffold.
version: 1.0.0
---

# Project Bootstrap

Use `modules.bootstrap.generator` when a user explicitly wants the opinionated
TypeScript/Vite scaffold supplied by this repository.

```powershell
python -m modules.bootstrap.generator --list
python -m modules.bootstrap.generator --target <project-root>
python -m modules.bootstrap.generator --target <project-root> --force
python -m modules.bootstrap.generator --validate --target <project-root>
```

The generator writes nine templates: ESLint, Prettier, Prettier ignore,
Husky pre-commit, CI, deployment, TypeScript, Vite, and Vitest configuration.
Without `--force`, existing files are preserved.

`--validate` checks file presence and required content markers. It does not run
`npm install`, ESLint, TypeScript, Vitest, a build, or deployment. Run those
commands separately in the target project before claiming the scaffold works.

There is no `/bootstrap` Slash Command in the current repository and Prompt
context detection does not automatically execute this module.
