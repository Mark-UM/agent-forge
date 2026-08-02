---
description: Security rules when touching markconfig/ directory
directory: markconfig
priority: 90
version: 1.5.0
---

# Secrets Context (CRITICAL)

## Iron Rules
- **NEVER** modify `markconfig/secrets.json` without explicit user request
- **NEVER** commit `secrets.json` to git (it's in `.gitignore`)
- **NEVER** print API keys to stdout/stderr/logs
- **NEVER** hardcode API keys in source files

## When modifying markconfig/ files
1. Confirm with user first (explicit request required)
2. Make backup before edit
3. Validate JSON syntax after edit
4. Verify keys are still environment-variable compatible

## When referencing secrets in code
- Read from environment variables: `os.environ.get("KEY_NAME")`
- Or load from `secrets.json` at runtime: `json.load(open("markconfig/secrets.json"))`
- Never inline keys as string literals

## When logging
- Redact API keys in log output: replace with `***`
- Use `modules/search/privacy.py` patterns for PII redaction
- Never log full request bodies with `Authorization` headers

## Failure mode
If you accidentally expose a key:
1. Stop immediately
2. Notify user
3. Help user rotate the key
4. Add regression test to prevent recurrence
