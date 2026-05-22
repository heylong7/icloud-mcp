---
name: email-organizer
description: Extract time-sensitive events from iCloud Mail and sync them to iCloud Calendar using LLM APIs.
trigger: 整理邮件, 检查截止日期, 提取邮件事件, 扫描邮件时间, 邮件有什么日程, organize email, check deadlines, extract events
---

# Email Organizer Skill

## Overview

This skill extracts time-sensitive events (meetings, deadlines, workshops, etc.) from your iCloud Mail inbox and syncs them to your iCloud Calendar. It uses LLM APIs to parse email content and identify events with specific dates and times.

## Prerequisites

- iCloud Mail with app-specific password configured in `.env`
- LLM API key (DeepSeek or OpenAI) configured in `.env`
- MCP server running: `python server.py`

## Workflow

### Step 1: Extract Events

Call the MCP tool to extract events from unread emails:

```
extract_events_from_emails(mailbox="INBOX", limit=50, since_days=3, auto_create=false)
```

### Step 2: Display Results

Group results by `time_clarity`:

**Explicit Events (auto-processed):**
Show these first with a checkmark. These have clear dates and times.

**Vague Events (needs confirmation):**
Show these with a warning icon. These have implied timing and need user confirmation.

### Step 3: User Confirmation

Ask the user:

1. "Confirm these explicit events to be written to calendar?" (list events)
2. For vague events: "These events have unclear timing, still write to calendar?" (list events, let user pick)

### Step 4: Write to Calendar

For confirmed events, call:

```
create_event(calendar_name_or_url="Calendar", summary="...", start="...", end="...", ...)
```

### Step 5: Summary

Report:
- X events created in calendar
- Y events skipped (duplicates)
- Z events pending (vague, not confirmed)

## Example Interaction

```
User: organize email

Claude: Scanning unread emails from the last 3 days...
Found 12 unread emails, extracted 3 events:

Explicit events (2):
1. Q3 Planning Meeting — June 15 14:00-16:00, Room 301
2. Paper Deadline — June 20 23:59

Needs confirmation (1):
3. Coffee catch-up — "sometime next week" (from: friend@personal.com)

Write these 2 explicit events to calendar?
```

## Configuration Reference

The skill uses these MCP tools (from the running server):
- `extract_events_from_emails` — extract events from unread emails
- `list_events` — check for existing events
- `create_event` — write events to calendar
- `list_messages` — browse email content
- `get_message` — read full email body
