---
description: Schedule management — create/list/delete/extract action items to schedules
agent: build
---

Manage scheduled tasks and action items via the schedule_store SQLite database.

## Usage

### Task CRUD

- `/schedule add <title> --due=<iso> [--priority=medium] [--desc=<text>] [--recurrence=<cron>]` — Create a new schedule entry
- `/schedule list [--status=pending] [--priority=high] [--days=7] [--source=user_manual]` — List schedules with filters
- `/schedule done <id>` — Mark a schedule as done
- `/schedule remove <id>` — Delete a schedule
- `/schedule show <id>` — Show details of a specific schedule
- `/schedule stats` — Show statistics (total/pending/done)

### Action Extraction

- `/schedule extract <report_path>` — Extract action items from a Markdown report using DeepSeek Flash

## Examples

```
/schedule add "完成 FIT2004 作业" --due=2026-08-01T23:59:00+08:00 --priority=high
/schedule add "周报" --due=2026-07-28T09:00:00+08:00 --recurrence="0 9 * * 1"
/schedule list --status=pending --days=7
/schedule list --priority=high
/schedule done abc123-def456
/schedule extract _runtime/reports/weekly_report.md
/schedule stats
```

## Implementation

### Task CRUD (schedule_store)

```python
from modules.orchestrator.schedule_store import (
    add_schedule, list_schedules, update_schedule_status,
    delete_schedule, get_schedule
)

# Add task
sid = add_schedule(
    title="完成作业",
    due_at="2026-08-01T23:59:00+08:00",
    source="user_manual",
    priority="high",
    description="FIT2004 Assignment 2",
    recurrence="0 9 * * 1"  # optional cron for recurring
)

# List with filters
items = list_schedules(status="pending", days=7, priority="high")

# Mark done
update_schedule_status(sid, "done")

# Delete
delete_schedule(sid)
```

### Action Extraction (action_extractor)

```python
from modules.orchestrator.action_extractor import extract_from_file

# Extract action items from a Markdown report
# Uses DeepSeek Flash API + PII redaction
# Automatically writes extracted items to schedules table
items = extract_from_file("_runtime/reports/weekly_report.md")
# Returns: [{title, due_at, priority, description, schedule_id}]
```

## Database

- **Location**: `_runtime/mcp-sqlite.db`
- **Table**: `schedules` (auto-created on first access)
- **Schema**: id (uuid), title, description, due_at (ISO 8601), priority (low/medium/high/critical), status (pending/done/skipped/cancelled), source (agent_extracted/user_manual/recurring), source_ref, recurrence (cron), created_at, updated_at, notified_at, notify_count

## Notes

- `due_at` must be ISO 8601 with timezone (e.g., `2026-08-01T23:59:00+08:00`)
- `priority` must be one of: low, medium, high, critical
- `source` must be one of: agent_extracted, user_manual, recurring
- Action extraction requires `DEEPSEEK_API_KEY` environment variable
- Extracted items default to `source=agent_extracted` with 7-day due_at if not specified
