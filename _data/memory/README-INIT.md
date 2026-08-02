# Memory Files

This directory separates safe tracked templates from ignored local memory.

Tracked files:

- `MEMORY.example.md` — safe starter template;
- `README-INIT.md` — this policy.

Ignored local files may include:

- `MEMORY.md` and `user-*.md` for private structured context;
- `lessons.md` and `decisions.md`, written by `modules.memory.hook` when that
  helper is explicitly called.

`markconfig/profile.md` is the profile loaded by `opencode.json`. Memory files
are not automatically loaded by that configuration. An agent may read selected
memory files through the `memory-context` skill when the task requires personal
context.

Do not store API keys here. Private memory is plain text and depends on local
filesystem permissions plus `.gitignore`.
