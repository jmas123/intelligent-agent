# System Architecture

## Data flow
[paste the revised architecture from our conversation]

## Layer decisions
- Ingestion: Gmail Pub/Sub push, Google Calendar push, Moodle iCal
- Filter: keyword match → sender heuristics → regex date extraction (~85% dropped)
- AI: structured output with fixed schema (see @docs/schemas.md)
- Store: SQLite, local-first, no raw content stored
- Output: launchd daemon, UNUserNotification (macOS), rumps menubar

## What we explicitly excluded and why
- iMessage: sandboxed on macOS, no API
- Discord: requires bot perms in others' servers
- LMS scraping: Moodle has real APIs, scraping is fragile + ToS violation
- Polling: Gmail Pub/Sub and GCal both support push; polling is unnecessary