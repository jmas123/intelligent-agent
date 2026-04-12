# Deadline Intelligence Agent

## What this is

A local-first personal intelligence runtime. Not a productivity tool.

This system builds a persistent, compounding model of how you work, live, and make decisions.
It reads your environment — email, calendar, files, voice, communication patterns — and develops
genuine understanding of your behavior over time. The longer it runs, the more it knows.
The more it knows, the more it can act as an ambient thinking partner, not just a task manager.

The end state is a system that knows your cycles, predicts your behavior, detects when you're
off-track before you do, and helps you close the gap between who you are on your worst weeks
and who you're trying to be.

All of this runs locally. Your identity model never leaves your device.

---

## Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| API server | FastAPI |
| Local AI | Ollama (primary) |
| Cloud AI fallback | Anthropic API (opt-in) |
| Voice input | OpenAI Whisper (local) |
| Embeddings | sentence-transformers |
| Storage | SQLite via SQLAlchemy (local-first) |
| Menubar | rumps |
| Chat UI | Open WebUI |
| Daemon | launchd (macOS), systemd (Linux) |

---

## Architecture

```
Sensors
  Gmail webhooks · Google Calendar push · FSEvents file watcher · Whisper voice
        │
        ▼
  State-change bus (internal pub/sub)
        │
        ▼
  Reasoning engine (Ollama-first → Anthropic fallback)
        │
   ┌────┴─────────────────────────────┐
   ▼                                  ▼
Identity layer                   Action layer
  BehavioralPatterns               ProposedActions
  LifeContext / mode               Calendar write
  KnowledgeGraph                   Gmail drafts
  WeeklySnapshots                  Scheduling negotiation
  DurableIdentityModel
        │
        ▼
  Ambient layer
    macOS notifications · menubar · session framing · voice output
```

Full design: `@docs/architecture.md`

---

## Commands

### Dev
```bash
make dev                    # start FastAPI server + Ollama
make test                   # pytest with coverage
make lint                   # ruff + mypy
make daemon-install         # install launchd plist
make openwebui-start        # start Open WebUI chat interface
```

### CLI
```bash
deadline-agent status                          # natural language state summary
deadline-agent ask "<question>"                # direct query against full state context
deadline-agent activity                        # file activity + linked deadlines
deadline-agent patterns                        # learned behavioral patterns
deadline-agent goals add|list|achieve|abandon  # intention tracking
deadline-agent decide "<decision>"             # log a decision
deadline-agent outcome <id> "<result>"         # record decision outcome
deadline-agent context set|show               # view or override life context
deadline-agent actions approve|reject <id>    # action queue management
deadline-agent debrief [--term "Spring 2025"] # semester retrospective
deadline-agent chat                            # open Open WebUI in browser
```

---

## Key Constraints

**Data handling**
- Never persist raw email or message content — extracted fields only
- AI extraction must use JSON schema / tool calling, never free-form text
- Behavioral patterns and identity model persist indefinitely — they are the system's memory
- Insights and suggestions expire — they are ephemeral observations, not permanent state

**AI model policy**
- Local Ollama model is always default
- Anthropic API is opt-in fallback, never primary
- All reasoning is local-first; cloud calls are logged and auditable

**Action model**
- Default is propose-only — the system never acts without approval
- Action permission model is config-driven: `~/.deadline-agent/config.toml`
- Gmail drafts are never auto-sent under any circumstances
- Calendar writes require explicit approval via menubar or CLI

**Privacy-sensitive features (opt-in via config)**
- `enable_tone_analysis` — outgoing email tone + response latency analysis
- `enable_social_graph` — relationship health tracking from email + calendar
- `enable_browser_tracking` — consumption pattern tracking (requires browser extension)

---

## Data Models (key)

```
Task                  — extracted deadline or action item
WorkSession           — inferred from file activity timestamps
BehavioralPattern     — persistent learned behavior (focus quality, lead time, effort accuracy)
LifeContext           — current season: recruiting | exams | crunch_week | burnout | default
StateSnapshot         — point-in-time unified context for reasoning engine
Insight               — LLM-generated suggestion, expires after surfacing
WeeklySnapshot        — compressed weekly summary, stored permanently
KnowledgeGraph        — entities (companies, professors, projects) + relationships
DurableIdentityModel  — persistent "about you" document, updated as system learns
Goal                  — stated intention with target metric + behavioral gap tracking
Decision              — logged decision with alternatives, outcome, pattern analysis
Relationship          — contact health: interaction trend, deprioritization signals
ProposedAction        — pending action awaiting approval (calendar block, draft, etc.)
NegotiationSession    — multi-turn scheduling conversation with conflict resolution
SemesterRecord        — term-level analytics: lead time, effort accuracy, crunch patterns
```

---

## Config

User config lives at `~/.deadline-agent/config.toml`.

Key fields:
```toml
extraction_model = "llama3.2:3b"        # swap in LoRA-tuned model here
reasoning_model  = "llama3.2:3b"        # swap in digest/reasoning LoRA here

enable_tone_analysis   = false          # opt-in: outgoing email analysis
enable_social_graph    = false          # opt-in: relationship health model
enable_browser_tracking = false         # opt-in: consumption patterns (needs extension)

[action_permissions]
calendar_write = "propose"              # propose | auto | deny
gmail_draft    = "propose"
```

---

## Identity Model

The durable identity model (`~/.deadline-agent/identity.json`) is the core artifact
this system builds over time. It is a persistent, structured document capturing:

- Behavioral rhythms (peak hours, focus quality, effort estimation accuracy)
- Life cycle patterns (how you behave in crunch vs. recruiting vs. light weeks)
- Relationship health and deprioritization tendencies under pressure
- Stated goals vs. actual behavior gaps
- Decision history and outcome patterns
- Blind spots — things you consistently underestimate, avoid, or get wrong

This document feeds every reasoning prompt. It is what separates a smart tool from
something that genuinely knows you. Back it up.

---

## Current Phase

See `@docs/roadmap.md` — currently Phase 21-22 (consumption tracking + LoRA fine-tuning).

### Next priorities
1. Browser extension for Phase 21 consumption tracking
2. QLoRA training pipeline (unsloth/axolotl) for extraction + reasoning LoRAs
3. Job application tracker (auto-built from Gmail pipeline)
4. Pre-meeting briefings (calendar event → context brief)
5. What-if simulation ("what does my week look like if I take this interview?")