# Deadline Agent — Available Commands

## Make Targets

| Command | What it does |
|---|---|
| `make dev` | Start the FastAPI server (port 8000) with hot reload |
| `make test` | Run pytest with coverage |
| `make lint` | Run ruff + mypy checks |
| `make daemon-install` | Install the background daemon via launchd |
| `make daemon-uninstall` | Remove the daemon |
| `make daemon-status` | Check if the daemon is running |
| `make menubar-install` | Install the macOS menubar app via launchd |
| `make menubar-uninstall` | Remove the menubar app |
| `make widget` | Launch the desktop widget directly |
| `make widget-install` / `widget-uninstall` | Install/remove the widget as a launchd service |
| `make openwebui-start` | Start Open WebUI (chat interface) via Docker on port 3000 |
| `make openwebui-stop` | Stop Open WebUI |

## CLI Commands (`deadline-agent ...`)

| Command | What it does |
|---|---|
| `list` | List extracted tasks/deadlines |
| `show` | Show details of a specific task |
| `mark` | Update a task's status (done/dismissed) |
| `digest` | Morning digest of upcoming deadlines |
| `alerts` | Manage pre-deadline alerts (24h, 2h) |
| `fetch-moodle` | Pull tasks from Moodle iCal feeds |
| `auth` | Manage OAuth/credentials (Gmail, Google Calendar) |
| `config` | Manage user config (`~/.deadline-agent/config.toml`) |
| `notify` | Send a test macOS notification |
| `activity` | View file activity and linked deadlines |
| `insights` | View/manage LLM-generated suggestions |
| `status` | Natural language summary of your current state |
| `ask` | Ask a natural language question about your workload |
| `schedule` | Propose a smart schedule from calendar gaps + task urgency |
| `weekly-review` | Weekly summary with pattern detection |
| `actions` | Manage proposed actions (calendar blocks, drafts) |
| `chat` / `widget` | Open the floating desktop widget UI |

## Chat API (when `make dev` is running)

Endpoints at `localhost:8000/api/chat/` for tasks, context snapshots, digest, file activity, actions, and natural language queries — designed for Open WebUI integration.

## What's Left to Build

Phases 1-4.5 are complete. Remaining work:

- **Phase 5**: File system awareness (FSEvents watcher, file-to-task linking, "no work detected" alerts)
- **Phase 6**: Proactive reasoning engine (state snapshots, LLM-generated insights)
- **Phase 7**: Write actions (Google Calendar time blocks, Gmail draft creation, approval flow)
- **Phase 8**: Cross-domain reasoning, smart scheduling, weekly reviews via chat
