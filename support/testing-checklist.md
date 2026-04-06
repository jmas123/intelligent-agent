# Deadline Agent — Functional Testing Checklist

## 1. Basic CLI (no setup needed)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 1 | CLI loads | `deadline-agent --help` | Shows all commands |
| 2 | Config init | `deadline-agent config init` | Creates `~/.deadline-agent/config.toml` |
| 3 | Config display | `deadline-agent config show` | Shows settings with masked secrets |
| 4 | Empty task list | `deadline-agent list` | Shows empty list or table header, no crash |
| 5 | Digest on empty DB | `deadline-agent digest` | Graceful "no upcoming tasks" or empty digest |
| 6 | Test notification | `deadline-agent notify "Hello from Deadline Agent"` | macOS notification appears |
| 7 | Status on empty DB | `deadline-agent status` | Returns a summary (even if trivial) |

## 2. Server & Health

| # | Test | Command | Expected |
|---|------|---------|----------|
| 8 | Server starts | `make dev` | Uvicorn boots on port 8000, no errors |
| 9 | Health check | `curl http://localhost:8000/health` | 200 OK / JSON response |
| 10 | Chat context (empty) | `curl http://localhost:8000/api/chat/context` | JSON with empty task arrays, no crash |
| 11 | Chat digest | `curl http://localhost:8000/api/chat/digest` | Returns digest text |
| 12 | Chat tasks list | `curl http://localhost:8000/api/chat/tasks` | Returns empty list or tasks |

## 3. Moodle Ingestion (needs a Moodle iCal URL)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 13 | Fetch Moodle feed | `deadline-agent fetch-moodle "<your-ical-url>"` | Tasks extracted and stored |
| 14 | Verify tasks stored | `deadline-agent list --source moodle` | Shows Moodle tasks with titles and due dates |
| 15 | Task detail | `deadline-agent show <task_id>` | Full details: title, course, due date, urgency, confidence |
| 16 | Mark task done | `deadline-agent mark <task_id> done` | Status changes to done |
| 17 | Mark task dismissed | `deadline-agent mark <task_id> dismissed` | Status changes to dismissed |
| 18 | Filter by status | `deadline-agent list --status done` | Only shows done tasks |
| 19 | Dedup check | Run `fetch-moodle` again with the same URL | No duplicate tasks created |

## 4. Google OAuth & Gmail/Calendar (needs OAuth credentials)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 20 | OAuth setup | `deadline-agent auth google` | Browser opens, paste code, tokens saved |
| 21 | Gmail webhook | Send yourself an email with "deadline" or "due" in the subject, then check `deadline-agent list --source gmail` | Task extracted from email |
| 22 | Calendar webhook | Create a Google Calendar event, check `deadline-agent list --source gcal` | Task extracted from event |
| 23 | Pre-filter works | Send yourself a non-deadline email (e.g., "Hey what's up") | No task created (filtered out) |

## 5. AI Extraction (needs Ollama running)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 24 | Ollama reachable | `curl http://localhost:11434/api/tags` | Lists available models |
| 25 | Extraction quality | Ingest a message with a clear deadline, check `deadline-agent show <id>` | Correct title, due date, urgency score, confidence >= 0.75 |
| 26 | Low confidence discard | Ingest a vague message (no clear deadline) | No task created or confidence < 0.6 flagged |
| 27 | Anthropic fallback | Stop Ollama (`killall ollama`), then ingest a task | Falls back to Anthropic API (if key configured), task still extracted |

## 6. Notifications & Alerts

| # | Test | Command | Expected |
|---|------|---------|----------|
| 28 | Manual alert check | `deadline-agent alerts check` | Sends alerts for tasks due within 24h/2h |
| 29 | 24h alert | Have a task due in ~20 hours, run alerts check | macOS notification: "Due in X hours" |
| 30 | No duplicate alerts | Run `deadline-agent alerts check` again | Same task does NOT get alerted twice |
| 31 | Morning digest (manual) | `deadline-agent digest` | Lists upcoming tasks in readable format |

## 7. File Activity (needs `watch_directories` configured)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 32 | File watcher starts | Start server with `watch_directories = ["~/Documents"]` in config | Logs show watcher started |
| 33 | File change detected | Create/edit a `.pdf` or `.docx` in a watched directory | `deadline-agent activity list` shows the file |
| 34 | File-task linking | Name a file similarly to a task title | `deadline-agent activity links` shows the association |
| 35 | Ignored patterns | Create a file in `.git/` or `__pycache__/` | NOT shown in activity list |
| 36 | No-work alert | Have a task due within 72h with no linked files, run alerts | "No work detected" notification |

## 8. Insights & Reasoning

| # | Test | Command | Expected |
|---|------|---------|----------|
| 37 | Generate insights | `deadline-agent insights generate` | Insights created (needs tasks in DB + Ollama) |
| 38 | List insights | `deadline-agent insights list` | Shows generated insights |
| 39 | Dismiss insight | `deadline-agent insights dismiss <id>` | Insight marked dismissed |
| 40 | Natural language query | `deadline-agent ask "What should I focus on today?"` | LLM-generated answer referencing your tasks |
| 41 | Weekly review | `deadline-agent weekly-review` | Summary of done/slipped/upcoming |

## 9. Smart Scheduling & Actions (needs Google Calendar OAuth)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 42 | Propose schedule | `deadline-agent schedule` | Shows proposed time blocks based on calendar gaps |
| 43 | List actions | `deadline-agent actions list` | Shows pending proposals |
| 44 | Approve action | `deadline-agent actions approve <id>` | Calendar event created in Google Calendar |
| 45 | Reject action | `deadline-agent actions reject <id>` | Action status changes to rejected |

## 10. UI

| # | Test | Command | Expected |
|---|------|---------|----------|
| 46 | Widget launches | `deadline-agent widget` | Floating window appears |
| 47 | Widget hotkey | Press **Cmd+Shift+D** | Widget toggles visibility |
| 48 | Menubar app | `make menubar-install`, check menu bar | "DA" icon appears with task count |
| 49 | Menubar actions | Click a task in menubar -> "Mark Done" | Task status updates |
| 50 | Open WebUI | `make openwebui-start`, visit `http://localhost:3000` | Chat interface loads |

## 11. Chat API (with server running)

| # | Test | Command | Expected |
|---|------|---------|----------|
| 51 | Update status via API | `curl -X PATCH localhost:8000/api/chat/tasks/<id>/status -H 'Content-Type: application/json' -d '{"status":"done"}'` | 200, task updated |
| 52 | Query via API | `curl -X POST localhost:8000/api/chat/query -H 'Content-Type: application/json' -d '{"question":"What is due today?"}'` | LLM-generated answer |
| 53 | Insights via API | `curl localhost:8000/api/chat/insights` | JSON list of insights |
| 54 | Calendar gaps | `curl localhost:8000/api/chat/calendar-gaps` | Free windows in next 7 days |

## 12. Daemon Lifecycle

| # | Test | Command | Expected |
|---|------|---------|----------|
| 55 | Install daemon | `make daemon-install` | Plist installed, daemon loaded |
| 56 | Check daemon status | `make daemon-status` | Shows `deadline-agent` in launchctl list |
| 57 | Daemon runs on boot | Log out and back in, check `make daemon-status` | Still running |
| 58 | Uninstall daemon | `make daemon-uninstall` | Daemon stopped and plist removed |
