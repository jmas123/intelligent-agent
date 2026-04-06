# Task Schema

Claude must extract tasks using this exact schema via tool calling / structured output.
Never use free-form text generation for extraction.
```json
{
  "title": "string — short human-readable name",
  "due_date_iso": "ISO 8601 or null if not found",
  "source": "gmail | moodle | gcal | slack | notion",
  "type": "assignment | exam | meeting | reminder | announcement",
  "course": "string or null",
  "urgency_score": "1-5 integer (5 = exam tomorrow, 1 = informational)",
  "confidence": "0.0-1.0 — how confident extraction is",
  "raw_hash": "sha256 of source content for dedup"
}
```

Discard if confidence < 0.6. Flag for review if 0.6–0.75.