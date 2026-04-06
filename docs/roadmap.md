# Build Roadmap

## Phase 1 — Extraction pipeline (no UI)
- [x] Gmail Pub/Sub webhook receiver (FastAPI endpoint)
- [x] Pre-filter: keyword + sender heuristics
- [x] Moodle iCal feed reader
- [x] Ollama structured extraction (JSON schema)
- [x] SQLite task store schema
- [x] CLI to print extracted tasks

## Phase 2 — Deduplication + daemon
- [x] Content hash dedup
- [x] Embedding similarity for near-dupes
- [x] launchd plist + install script
- [x] Google Calendar push webhook
- [x] OAuth token management with auto-refresh

## Phase 3 — Notifications + UI
- [x] macOS notifications (osascript)
- [x] rumps menubar app
- [x] Morning digest summary
- [x] Pre-deadline alerts (24h, 2h)
- [x] Alert + digest schedulers in FastAPI lifespan

## Phase 4 — Polish
- [x] Anthropic API fallback when Ollama fails
- [x] User config file (~/.deadline-agent/config.toml)
- [ ] Cross-platform port (deferred — macOS-only for now)

## Phase 4.5 — Chat UI foundation (Open WebUI + Ollama)
- [x] Chat API router: GET `/api/chat/tasks`, `/api/chat/tasks/{id}`, `/api/chat/context`, `/api/chat/digest`
- [x] PATCH `/api/chat/tasks/{id}/status` for marking done/dismissed via chat
- [x] `TaskRepository` query helpers: `list_tasks_due_today()`, `list_overdue_tasks()`, `count_by_status()`
- [x] Open WebUI tool scripts: `deadline_tasks.py` (task CRUD + context), `deadline_digest.py`
- [x] System prompt for Ollama (auto-call context snapshot on new chat)
- [x] `make openwebui-start` target

## Phase 5 — File system awareness (read-only)
- [x] FSEvents watcher for configurable directories (~/Documents, ~/Downloads, project dirs)
- [x] `FileActivity` model: path, last_modified, size, project association
- [x] Heuristic linker: match file paths/names to task titles/courses
- [x] "No work detected" alerts when deadline <72h and no matching file activity
- [x] CLI: `deadline-agent activity` to show file activity + linked deadlines
- [x] Chat: GET `/api/chat/file_activity` endpoint + Open WebUI tool
- [x] Chat: context snapshot gains `recent_file_activity` and `unworked_deadlines`

## Phase 6 — State model + proactive reasoning
- [x] Periodic user state snapshot (deadlines, calendar gaps, file activity, comms)
- [x] Reasoning engine: state snapshot → LLM → prioritized suggestions (Ollama-first)
- [x] `Insight` model: type, content, related task IDs, dismissed flag
- [x] Morning digest v2: LLM-generated prioritized briefing replacing plain task list
- [x] CLI: `deadline-agent status` for natural language state summary
- [x] Chat: POST `/api/chat/query` — natural language question with full state context
- [x] Chat: context snapshot gains `insights` (LLM-generated suggestions)
- [x] Chat: Open WebUI Filter for auto-context injection on new chats

## Phase 7 — Write actions (calendar + drafts)
- [x] Action permission model: config-driven allow/deny, default propose-only
- [x] Google Calendar write: propose + create time blocks on approval
- [x] Gmail draft creation: propose follow-up drafts, never auto-send
- [x] `ProposedAction` model + action queue (proposed → approved → executed)
- [x] Menubar v2: proposed actions section with approve/reject
- [x] CLI: `deadline-agent actions [approve|reject] <id>`
- [x] Chat: `/api/chat/actions/*` endpoints (propose, approve, reject)
- [x] Chat: Open WebUI tool `deadline_actions.py` (propose calendar blocks, approve actions)
- [x] Chat: Menubar "Open Chat" item → opens Open WebUI in browser

## Phase 8 — Cross-domain reasoning + natural language interface
- [x] Unified context builder: tasks + calendar + files + comms, relevance-scored
- [x] Smart scheduling: calendar gaps + task urgency + effort estimation → proposed schedule
- [x] Weekly review: done/slipped/upcoming summary with pattern detection
- [x] Chat: `deadline-agent chat` CLI command opens Open WebUI
- [x] Chat: `deadline-agent ask "<question>"` calls `/api/chat/query` directly

## Phase 9 — Behavioral memory (learns over time)
- [x] `WorkSession` model: task_id, started_at, ended_at, inferred from file activity timestamps
- [x] Actual vs estimated effort tracking: compare `estimate_effort()` predictions to real `WorkSession` durations
- [x] Peak hours analysis: aggregate file activity timestamps → detect user's productive windows
- [x] Lead time tracking: time between task `created_at` and first linked `FileActivity`
- [x] `BehavioralPattern` model: pattern_type, value, confidence, updated_at (persistent, not ephemeral)
- [x] Extend `clear_stale()` to preserve patterns — insights expire, learned behaviors don't
- [x] Feed behavioral patterns into reasoning engine prompts ("you usually underestimate discussion posts")
- [x] CLI: `deadline-agent patterns` to show learned behaviors
- [x] Chat: `/api/chat/patterns` endpoint + widget Patterns tab

## Phase 10 — Life context model
- [x] `LifeContext` model: season (recruiting, exams, light_week, default), active date range, user-set or inferred
- [x] Config: `life_contexts` section in config.toml for manual season definitions
- [x] Auto-detection heuristics: exam period if 3+ exams within 10 days, recruiting if interview-related calendar events
- [x] Extend `StateSnapshot` with active life context — surfaces in all reasoning prompts
- [x] Reasoning engine prompt update: "You're in recruiting season with 4 interviews this week" colors all recommendations
- [x] Non-academic task type support: interview_prep, networking, personal
- [x] CLI: `deadline-agent context [set|show]` to view/override current life context
- [x] Chat: life context shown in context snapshot and digest

## Phase 11 — Scheduling negotiation
- [x] On 409 conflict: auto-query `fetch_free_busy()` for nearest available slots and return alternatives
- [x] `NegotiationSession` model: conversation_id, proposed_actions, counter_proposals, resolution
- [x] Multi-turn scheduling in chat: "move the 8pm block to 10:30" without starting over
- [x] Priority-aware rescheduling: "this interview is more important — what can I push?"
- [x] Widget Actions tab: show alternative time slots inline when conflict detected
- [x] Chat: `/api/chat/negotiate` endpoint for multi-turn scheduling conversations

## Phase 12 — Semester debrief
- [x] `SemesterRecord` model: term name, start/end dates, summary stats (auto or user-defined)
- [x] Lead time report: average days between task posted and work started, per course
- [x] Effort accuracy report: estimated vs actual hours, per task type
- [x] Crunch analysis: which courses caused last-minute work spikes
- [x] Workload distribution chart data: tasks completed per week across the semester
- [x] Procrastination trend: are you starting earlier or later over time?
- [x] LLM-generated narrative retrospective with honest observations
- [x] CLI: `deadline-agent debrief [--term "Winter 2026"]`
- [x] Chat: `/api/chat/debrief` endpoint + widget Debrief tab
