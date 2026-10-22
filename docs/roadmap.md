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

## Phase 13 — Anticipatory triggers (reactive → state-change driven)
- [x] Event-driven reasoning: file watcher + Gmail webhook trigger insight generation on state change, not just timer
- [x] Activity baseline tracking: rolling 7-day file activity average, detect drops >30%
- [x] Compound trigger engine: "3 deadlines Friday + activity dropped 40% this week" → unprompted Wednesday nudge
- [x] Recruiting email fast-path: new email from tracked company → surface immediately via notification, before user opens Gmail
- [x] Peak window nudge: "your productive window starts in 20 min + you have an unstarted assignment due tomorrow" → real-time alert
- [x] State-change bus: internal pub/sub so any module (file watcher, email ingester, calendar) can emit events that trigger reasoning

## Phase 14 — Life awareness (task-aware → life-aware)
- [x] Health inference from existing data: detect consecutive late-night sessions (1AM+ work 3+ days), zero-activity days, irregular patterns
- [x] Burnout risk scoring: combine late nights + missed deadlines + activity drops into a single fatigue signal
- [x] Honest accounting alerts: "you've worked past midnight 3 nights running — your assignment quality drops 40% when you do this"
- [ ] Financial layer: tuition deadlines, scholarship windows, salary negotiation timelines — same extraction pipeline, new task types
- [x] Relationship tracker: people who need follow-up (recruiters, professors, references) — detect when someone important hasn't heard from you
- [x] Follow-up staleness alerts: "Professor X hasn't responded in 12 days — draft a follow-up?" (extends existing Gmail draft proposer)
- [x] Extend `LifeContext` auto-detection: infer "burnout", "crunch_week", "coasting" from behavioral signals

## Phase 15 — Genuine reasoning (summarization → thinking)
- [x] Causal reasoning in prompts: inject "based on your lead time of X days, you needed to start Tuesday — it's Wednesday, here's a recovery plan"
- [x] Tradeoff reasoning: cross-domain priority weighting — "Cisco is your only active response in 134 apps vs HW4 worth 8% of grade"
- [x] Longitudinal reasoning: feed `SemesterRecord` analytics + `BehavioralPattern` history into reasoning prompts for cross-semester pattern detection
- [x] Recovery plan generation: when deadlines are missed or at risk, generate a concrete catch-up schedule, not just a warning
- [x] Persistent weekly summaries: store weekly review as `WeeklySnapshot` model, compress into durable context for reasoning
- [x] Narrative continuity: each reasoning cycle receives compressed history of recent weeks, not just current state
- [x] Workload spike prediction: "week 12 pattern from last semester is repeating — you're in week 11 now with recruiting active"

## Phase 16 — Ambient presence (widget → always-there)
- [x] Local Whisper integration: voice input for "what should I focus on tonight" — on-device, private, always available
- [x] Proactive interrupts: file watcher detects you opened a coding project → "before you start, Cisco email from this morning still unread, 5 min"
- [x] Context switching detection: track life_track transitions (school → recruiting) → adjust state and surface relevant context
- [x] Mode-aware UI: "Recruiting mode — 3 applications started but not submitted. Want the status?"
- [x] Ambient notification channel: low-priority background awareness vs high-priority interrupts (not everything is a macOS notification)
- [x] Session framing: when you sit down to work, auto-generate a 30-second briefing of what matters right now

## Phase 17 — Memory layer (event memory → narrative memory)
- [x] `WeeklySnapshot` model: compressed weekly summary stored permanently, fed into reasoning context
- [x] Personal knowledge graph: entities (companies, professors, courses, projects) and relationships, growing over time
- [x] Time-sliced behavioral patterns: "in high-pressure weeks you deprioritize recruiting follow-ups" — not just aggregate patterns
- [x] Longitudinal self-knowledge: after a year, the model knows your tendencies better than you do — concrete, data-backed
- [x] Semester-over-semester comparison: "your procrastination improved 1.2 days vs last semester, but effort accuracy got worse"
- [x] Context compression pipeline: recent weeks in full detail, older weeks as summaries, oldest as patterns — sliding window
- [x] Durable identity model: persistent "about you" document that updates as the system learns — preferences, rhythms, blind spots

## Phase 18 — Focus quality + physical inference (activity → understanding)
- [x] Focus quality scoring: edit frequency vs net output (many saves, little size change = stuck signal)
- [x] Session fragmentation metric: unique files / session duration — fragmented vs sustained work detection
- [x] Inter-file-open interval tracking: gap analysis between distinct file paths to measure context switching
- [x] New `BehavioralPattern` types: `focus_quality`, `session_fragmentation` stored via `PatternRepository`
- [x] `_format_pattern()` renderers: "your sessions averaged 12 min before switching — fragmented vs 45-min baseline"
- [x] Sleep window inference: longest daily gap in `FileActivity` timestamps as sleep proxy
- [x] Sleep consistency scoring: stdev of sleep-start/end times across rolling 7-day window
- [x] All-nighter detection: no gap > 3h in a 24h period
- [x] Extend `health_signals` dict + `WeeklySnapshot.health_signals_json` with sleep data
- [x] Absence detection: rolling 4-week baselines for unscheduled evenings, personal project time, non-academic interactions
- [x] Absence signals fire when category drops to zero for 2+ weeks or falls 70%+ below baseline
- [x] StateSnapshot gains `absence_signals` in health_signals section
- [x] New event types: `FOCUS_QUALITY_UPDATED`, `SLEEP_PATTERN_DETECTED`, `ABSENCE_DETECTED`

## Phase 19 — Intention tracking + decision memory (behavior → self-knowledge)
- [x] `Goal` model: description, category (academic|recruiting|health|social|personal), target_metric, status
- [x] `GoalRepository`: CRUD + gap computation against real behavioral data
- [x] Goal tracker: `compute_goal_gaps(session)` compares active goals to actual FileActivity/BehavioralPattern data
- [x] CLI: `deadline-agent goals add|list|achieve|abandon`
- [x] StateSnapshot gains `goal_gaps: list[str]` → new "INTENTION vs BEHAVIOR" prompt section
- [x] `Decision` model: description, alternatives_considered (JSON), chosen_option, context_json, outcome, outcome_recorded_at
- [x] `DecisionRepository`: CRUD + outcome pattern analysis ("last 3 times you chose depth over breadth...")
- [x] CLI: `deadline-agent decide "chose X over Y"`, `deadline-agent outcome <id> "result"`
- [x] StateSnapshot gains `recent_decisions: list[str]` → new "DECISION HISTORY" prompt section
- [x] Chat: `/api/chat/goals` and `/api/chat/decisions` endpoints

## Phase 20 — Outgoing tone + social graph (communication → relationship awareness)
- [x] Tone analyzer: periodic scheduler (6h) fetches sent Gmail messages, extracts metadata only (length, response latency, recipient)
- [x] Response latency computation: match sent messages to received via In-Reply-To/thread ID
- [x] Contact frequency shift: rolling 2-week window deltas per recipient
- [x] New `BehavioralPattern` type `outgoing_tone`: "your email responses shortened 40% this week"
- [x] Config: `enable_tone_analysis: bool = False` (opt-in, privacy-sensitive)
- [x] `Relationship` model: person, channel (email|calendar), interaction_count, trend (growing|stable|atrophying), avg_response_time_hours
- [x] Social graph builder: email frequency + calendar attendee lists → relationship records
- [x] Trend computation: compare last 2 weeks to prior 2 weeks, flag 50%+ drops as atrophying
- [x] Deprioritization detection: flag contacts whose frequency dropped during active LifeContext seasons
- [x] StateSnapshot gains `relationship_alerts: list[str]` → new "RELATIONSHIP SIGNALS" prompt section
- [x] Config: `enable_social_graph: bool = False`
- [x] Chat: `/api/chat/relationships` endpoint

## Phase 21 — Consumption tracking (deferred — requires browser extension)
- [ ] `BrowsingActivity` model schema (behind feature flag)
- [ ] `POST /api/browsing` endpoint stub for receiving data from future browser extension
- [ ] Config: `enable_browser_tracking: bool = False`
- [ ] Classification taxonomy: research, entertainment, job boards, social media, coding reference
- [ ] Mode detection integration: browsing patterns feed into ambient state for predictive context switching
- [ ] Focus quality enrichment: application switching rate during work sessions from frontmost-app tracking

## Phase 22 — LoRA fine-tuning (generic model → your model)
- [x] Training data export: script to dump extraction pairs (input text → `ExtractedTask` JSON) from SQLite, filtered by confidence ≥ 0.75 + user-accepted
- [x] Digest training pairs: export (state snapshot → generated digest) pairs rated good by user feedback
- [ ] QLoRA training pipeline: `unsloth` or `axolotl` config for fine-tuning `llama3.2:3b` on M-series Mac
- [ ] Extraction LoRA: fine-tune on ~200-500 labeled extraction examples — fewer Anthropic fallbacks, higher confidence
- [ ] Reasoning/digest LoRA: fine-tune on ~50-100 digest/insight examples — internalize prompt style, consistent tone
- [x] GGUF export + Ollama Modelfile: package LoRA-merged model for direct use in Ollama (`deadline-extract:latest`)
- [x] Config integration: `extraction_model` and `reasoning_model` fields in config.toml to swap in fine-tuned models
- [x] Continuous improvement loop: accumulate new accepted/dismissed pairs → monthly retrain → push updated model
- [x] A/B evaluation harness: run base vs LoRA on held-out examples, compare confidence scores + schema validity rates

## Phase 23 — Behavioral simulation (retrospective → prospective)
- [ ] `SimulationEngine`: compose existing patterns (lead time, effort accuracy, peak hours, burnout signals) into forward projections
- [ ] "What-if" API: `POST /api/chat/simulate` — accepts a scenario description, returns projected impact on sleep, deadlines, health signals
- [ ] Scenario types: add_commitment (hours/week), drop_course, accept_offer, change_schedule
- [ ] Projection confidence scoring: flag when data is too sparse for reliable projection (< 1 semester of dense tracking)
- [ ] Feed `BehavioralPattern` history + `WeeklySnapshot` trends into projection model
- [ ] CLI: `deadline-agent simulate "what if I take a part-time job 15h/week"`
- [ ] Chat: natural language simulation via `/api/chat/query` with simulation context
- [ ] Widget: "What if..." button in dashboard that opens simulation prompt

## Phase 24 — Inner model (behavior → self-knowledge)
- [x] Task affect inference: classify tasks by engagement pattern — early start = enjoyment, last-minute = avoidance, skipped = anxiety
- [x] `TaskAffect` model: task_type, affect_label (enjoyment|neutral|avoidance|anxiety), confidence, evidence (start lag, completion pattern)
- [x] Avoidance detection: flag tasks/categories consistently started in final 10% of available time
- [x] Energy proxy: infer post-task state from what follows — long break after = draining, immediate next task = energizing
- [x] Feed affect labels into reasoning prompts: "you avoid ENG 331 essays (anxiety pattern) — the recommendation accounts for that"
- [x] Distinct interventions by affect: avoidance gets "start with 10 minutes to break the seal", anxiety gets "what specifically feels hard about this?"
- [x] CLI: `deadline-agent affects` to show inferred task-type affect map
- [x] Widget: affect indicators on task cards (subtle color/icon showing inferred relationship to task)

## Phase 25 — Active recruiting intelligence (tracker → advisor)
- [x] Application-to-response rate analytics: by company tier, role type, application method
- [x] Over-indexing detection: flag when 80%+ of applications target the same tier/sector with diminishing returns
- [x] Tier gap analysis: "you've applied to 15 Series B startups and 0 mid-cap — here's why that matters"
- [x] Resume variant effectiveness: which resume version correlates with higher response rates
- [x] Temporal pattern detection: day-of-week and time-of-day application success rates
- [x] Fit scoring (data-driven, not prescriptive): rank open applications by similarity to your successful ones
- [x] Weekly recruiting report: response rate trends, pipeline health, suggested next actions
- [x] CLI: `deadline-agent recruiting report`
- [x] Widget: recruiting analytics section in Pipeline tab with response rate charts
- [x] Chat: `deadline-agent ask "which companies should I follow up with"` uses pipeline analytics

## Phase 26 — Tension model (single context → competing demands) ⚡ FINISH THIS
- [ ] `ActiveTension` representation: pairs of competing demands (recruiting vs academics, health vs deadlines) with current allocation ratio
- [ ] Tension detection: when 2+ life contexts are active AND goal gaps show one winning, surface the tradeoff explicitly
- [ ] Allocation tracking: infer time-split between competing demands from file activity life_track classification
- [ ] Honest naming in reasoning prompts: "ACTIVE TENSIONS: recruiting is getting 60% of your peak hours, academics 25%, health 15% — is that intentional?"
- [ ] Intention-vs-allocation drift alerts: "you said health was a priority but it's gotten 0 hours this week"
- [ ] Rebalancing suggestions: when drift exceeds threshold, propose concrete schedule adjustments
- [ ] Weekly tension summary in weekly review: how you actually split time vs how you said you would
- [ ] Widget: tension indicators in dashboard mode banner — show competing demands, not just primary mode
- [ ] CLI: `deadline-agent tensions` to show active tensions and allocation ratios

---

# The Brain Loop

Everything above gives us features, models, and insights. What's below is the missing layer that makes it feel like a mind — a system that continuously decides what matters, when to speak, and how hard to push. These are the highest-leverage additions.

## Phase 27 — Priority Engine (features → single ranked answer) ✅ COMPLETE
The system generates insights but never answers the real question: "what should I do right now?"

- [x] `PriorityRanking` model: task/action, rank, risk_if_ignored, suggested_next_action, reasoning
- [x] Priority computation: combine deadline proximity, effort estimate, progress signals, life context, behavioral patterns, and risk scores into a single ranked list
- [x] Top-3 output format: what, why, risk if ignored, suggested next action
- [x] Example output: `1. Study for OS exam (risk: high, no activity, due in 2 days) 2. Reply to recruiter (relationship decay risk) 3. Finish NetApp module (on track)`
- [x] Feed into morning digest: replace generic summary with ranked priorities
- [x] Feed into menubar: top priority always visible
- [x] Feed into session start briefing (Phase 32): priorities anchor the briefing
- [x] Feed into voice response: "your top priority right now is..."
- [x] CLI: `deadline-agent priorities` — ranked list with risk and reasoning
- [x] API: `GET /api/priorities` — returns current ranked priorities
- [x] Chat: priority-aware responses — every query implicitly knows what matters most

## Phase 28 — Risk Prediction Engine (heuristics → probabilities) ✅ COMPLETE
We already have lead time, effort accuracy, patterns, and file activity. Formalize it into per-task risk scores.

- [x] `TaskRisk` model: task_id, probability_of_missing (0-1), hours_needed, hours_available, confidence_score, last_computed
- [x] Risk computation: for every active task, compute P(miss) from lead time patterns, effort accuracy history, current progress, and available calendar gaps
- [x] Hours-needed estimation: use `BehavioralPattern` effort accuracy to adjust raw estimates ("you underestimate essays by 1.8x")
- [x] Hours-available computation: scan calendar for free blocks between now and deadline, discount by historical focus quality
- [x] Confidence scoring: flag when data is sparse ("low confidence — first time seeing this task type")
- [x] Output format: "You are 72% likely to miss this unless you act today"
- [x] Risk feeds into Priority Engine (Phase 27): high-risk tasks rank higher
- [x] Risk feeds into Intervention Engine (Phase 29): risk level determines interrupt strength
- [x] Periodic recomputation: recalculate on state changes (new file activity, calendar change, deadline update)
- [x] CLI: `deadline-agent risk` — per-task risk breakdown
- [x] API: `GET /api/risk` — current risk scores for all active tasks
- [x] Widget: risk indicators on task cards (green/yellow/red with probability)

## Phase 29 — Intervention Engine (triggers → intelligent interrupts) ✅ COMPLETE
The decision layer between risk signals and notification delivery. Every notification path routes through the intervention engine.

- [x] `InterventionPolicy` config: max interrupts per day (interrupt budget), quiet hours, escalation thresholds
- [x] Priority threshold model: map risk levels to intervention strength
  - Low risk → silent (log only)
  - Medium risk → menubar update (passive)
  - High risk → macOS notification (active)
  - Critical risk → voice interrupt (urgent)
- [x] Interrupt budget tracker: count daily interrupts, refuse to fire low-value ones once budget is spent
- [x] Escalation logic: if a medium-risk item is ignored for 24h and risk increases, escalate to next tier
- [x] De-escalation: if user acts on something, cancel pending escalations for that item
- [x] Cooldown periods: no back-to-back interrupts within configurable window (default 30 min)
- [x] Context-aware suppression: don't interrupt during focus sessions, calendar events, or late-night hours
- [x] Interrupt effectiveness tracking: did the user act after the interrupt? Learn which intervention types work
- [x] Config: `[intervention]` section in config.toml — budget, thresholds, quiet hours, escalation rules
- [x] All existing notification paths (triggers, alerts, nudges) route through this engine

## Phase 30 — Passive Logging System (manual input �� ambient capture) ✅ COMPLETE
Reconstructs your day passively from file activity, calendar, and work sessions. Only asks when it can't tell.

### A. Daily auto-reconstruction
- [x] End-of-day timeline inference: combine file activity + calendar events + work sessions → inferred timeline
- [x] `DailyReconstruction` model: date, timeline_json (segments with confidence), user_confirmed, narrative
- [x] Present reconstruction via intervention engine notification at end of day
- [x] CLI: `deadline-agent day` — show today's inferred timeline with confidence indicators
- [x] Scheduler: runs at configurable time (default 23:00), persists daily

### B. Micro-confirmations
- [x] Low-friction confirmation prompts: "Was this real progress on [task]?" routed through intervention engine
- [x] Triggered for segments with confidence < threshold (default 0.6)
- [x] Confirmation responses adjust FileTaskLink confidence (yes: +0.1, no: -0.2)
- [x] Max 3 micro-confirmations per day, materiality check (only ask if deadline within 7d or active risk)

### C. Voice logging
- [x] Voice-to-task-update pipeline: record → Whisper transcribe → LLM tool-calling extraction → apply
- [x] Supports: task_complete, task_progress, context_switch, note
- [x] Fuzzy task title matching against pending tasks
- [x] CLI: `deadline-agent voice` — record up to 15 seconds, transcribe, extract, apply
- [ ] Always-listening mode (opt-in): deferred, needs continuous audio loop

### D. Confidence scores
- [x] All timeline segments carry confidence + confidence_sources
- [x] Low-confidence inferences trigger micro-confirmations instead of assertions
- [x] Confidence displayed in CLI: >= 0.8 solid, 0.6-0.8 "~", < 0.6 "?"
- [x] Materiality check: only ask when answer changes a recommendation

## Phase 31 — Simulation Engine v2 (projection → thinking partner) ✅ COMPLETE
Wraps the Phase 23 LLM narrative with deterministic risk recomputation. Every projection is grounded in real P(miss) deltas.

- [x] Natural language scenario input: LLM tool-calling parses "skip class" / "drop CS 301" into structured modifications
- [x] Deterministic risk recomputation: recompute P(miss) for every task under modified calendar/task state
- [x] Multi-variable projection: risk deltas + LLM narrative across academics, recruiting, health, schedule, social
- [x] Projected schedule visualization: calendar gaps recomputed under scenario modifications
- [x] Drop/add analysis: freed hours, net risk change, which tasks benefit from dropping
- [x] Failure probability per task under scenario: exact P(miss) before → after with delta
- [x] Feed Risk Prediction Engine (Phase 28) data into simulations for grounded projections
- [x] Conversational simulation: session_id accumulates modifications across follow-up turns
- [x] CLI: `deadline-agent simulate "<scenario>"` with `--follow-up` for chained what-ifs
- [x] API: `POST /simulate/v2` with risk_deltas, projected_schedule, drop_analysis in response

## Phase 32 — Session Start Intelligence (task list → thinking partner) ✅ COMPLETE
Phase 16 added session framing. This makes it the primary interface — when you sit down, it tells you what to think about.

- [x] 20-30 second structured briefing on session start, not a task dump
- [x] Format:
  ```
  You have 3 hours free.

  Most important:
  - OS exam (high risk, no progress)
  - Recruiter follow-up (stale 6 days)

  Recommendation:
  Start with 90 min OS prep now.
  ```
- [x] Pulls from: Priority Engine (Phase 27) for ranking, Risk Engine (Phase 28) for urgency, calendar for available time
- [x] Available time computation: look at calendar from now until next event or end of day
- [x] Time-block suggestion: break available time into recommended focus blocks based on priorities
- [x] Delivery channels: menubar popup (intervention engine), CLI, chat greeting, compact variant for voice
- [x] Session end summary: "you spent 2h on OS prep (good), skipped the recruiter follow-up (still stale)"
- [x] CLI: `deadline-agent start` — trigger session briefing manually
- [x] Auto-trigger: detect session start from first file activity after 30+ min gap (ambient state SESSION_STARTED)

---

# Current priorities (what to build next)

**Done (brain loop core):**
1. ~~Priority Engine (Phase 27)~~ ✅
2. ~~Risk Prediction Engine (Phase 28)~~ ✅
3. ~~Intervention Engine (Phase 29)~~ ✅
4. ~~Passive Logging (Phase 30)~~ ✅

**Build now (remaining brain loop):**
5. Finish Tension Model (Phase 26) — not started, high value
6. ~~Session Start Intelligence (Phase 32)~~ ✅
7. ~~Simulation Engine v2 (Phase 31)~~ ✅

**Defer:**
- Phase 21 (browser extension / consumption tracking) — not yet needed
- Phase 22 remaining items (QLoRA training) — need more data first
- More ingestion sources — we have enough signal
- More raw features or models — the bottleneck is reasoning, not data
- Heavy automation — propose-only model is correct for now
