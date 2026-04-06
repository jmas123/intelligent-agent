# Deadline Intelligence Agent

## Stack
- Python 3.12, FastAPI for webhook receiver
- SQLite (local-first) via SQLAlchemy
- Ollama (local) as primary AI, Anthropic API as fallback
- launchd daemon on macOS, systemd on Linux

## Commands
- `make dev` — start FastAPI server + Ollama
- `make test` — pytest with coverage
- `make lint` — ruff + mypy
- `make daemon-install` — install launchd plist

## Architecture (brief)
Webhooks → pre-filter → AI extraction (structured output) → SQLite → OS notifications
Full design: @docs/architecture.md

## Key constraints
- Never persist raw email/message content, only extracted fields
- AI extraction must use JSON schema / tool calling, never free-form text
- Local Ollama model is default; API is opt-in
- All ingestion sources use push (webhooks/subscriptions), not polling

## Current phase
See @docs/roadmap.md for what's in progress