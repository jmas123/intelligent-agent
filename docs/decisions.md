# Architecture Decisions

## ADR-001: Local Ollama as default AI
Chosen over API-first because: privacy (email content), zero marginal cost, works offline.
Fallback to Anthropic API if Ollama unreachable or confidence too low.

## ADR-002: SQLite over PostgreSQL
Single-user local tool. SQLite is zero-infrastructure, trivially backupable.
Migrate to Postgres only if multi-device sync becomes a requirement.

## ADR-003: Push webhooks over polling
Gmail Pub/Sub and GCal both support push. Polling adds latency and wastes quota.
Moodle uses iCal subscription (pull on schedule, but lightweight).

## ADR-004: Structured output for extraction
Free-form LLM text → fragile parsing. JSON schema via tool calling enforces the contract.
If model doesn't support tool calling, use system prompt with JSON-only instruction + validate.