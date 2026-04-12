"""LLM reasoning engine for generating proactive insights."""

import json
import logging
from typing import Any

from pydantic import BaseModel

from deadline_agent.config import settings
from deadline_agent.extraction.extractor import ExtractionError
from deadline_agent.models import Insight
from deadline_agent.reasoning.state import build_state_snapshot
from deadline_agent.store.insight_repository import InsightRepository

logger = logging.getLogger(__name__)

REASONING_SYSTEM_PROMPT = (
    "You are a proactive academic deadline assistant. Analyze the student's current state "
    "and generate actionable insights.\n\n"
    "If an 'ABOUT YOU' identity section is present, use it to deeply personalize your "
    "recommendations. Reference the student's known patterns, blind spots, and values "
    "by name — don't rediscover what the system already knows about them.\n\n"
    "Focus on:\n"
    "- Deadlines approaching with no work detected\n"
    "- Overdue tasks that need attention\n"
    "- Workload spikes (multiple things due close together)\n"
    "- Prioritization suggestions based on urgency and due dates\n"
    "- When behavioral patterns are available, reference them in recommendations "
    "(e.g., 'based on past assignments, you'll need about 3 hours, not the 2 you planned')\n\n"
    "- When a life context is active (e.g., recruiting season, exam period), prioritize "
    "recommendations accordingly. During exam season, deprioritize non-academic tasks. "
    "During recruiting, ensure interview prep is surfaced prominently.\n\n"
    "- When causal analysis is provided, use it to explain WHY a task is at risk, "
    "not just that it is. Reference the student's actual lead times and effort patterns.\n"
    "- When priority tradeoffs are provided, use them to rank recommendations. "
    "Cross-domain comparisons (school vs recruiting vs personal) should influence "
    "what you suggest the student focuses on first.\n"
    "- When cross-semester patterns are available, reference them explicitly. "
    "Compare current behavior to past semesters and call out regressions or improvements.\n"
    "- When workload spike predictions are present, lead with them as early warnings.\n"
    "- When TASK AFFECT labels are present, use them to personalize recommendations. "
    "For avoidance patterns, suggest 'start with just 10 minutes to break the seal'. "
    "For anxiety patterns, ask 'what specifically feels hard about this?'. "
    "For enjoyment patterns, suggest leveraging that energy as a warm-up before harder tasks. "
    "Reference the specific affect label and evidence.\n\n"
    "Be specific and actionable. Reference task names and dates. "
    "Generate 1-5 insights, prioritized by importance."
)

DIGEST_SYSTEM_PROMPT = (
    "You are a proactive academic deadline assistant writing a morning briefing.\n\n"
    "If an 'ABOUT YOU' identity section is present, let it shape your tone and advice. "
    "You know this person — speak to their specific patterns and tendencies.\n\n"
    "RULES:\n"
    "- Lead with the single most important thing the student needs to know right now.\n"
    "- Group related deadlines instead of listing them one by one "
    "(e.g., '3 things due Friday, none started' is better than 3 separate bullets).\n"
    "- If insights are provided, weave them into the narrative — they represent "
    "patterns the reasoning engine already detected (no-progress warnings, "
    "workload spikes, overdue clusters). Don't repeat them verbatim; synthesize.\n"
    "- Mention file activity context: if work has been detected on something, "
    "say so briefly; if nothing has been touched, flag it.\n"
    "- End with one concrete suggestion for what to tackle first and why.\n"
    "- If behavioral patterns are provided, weave relevant ones into recommendations "
    "(e.g., 'you tend to start discussion posts late — maybe tackle that first today').\n"
    "- Keep it to 3-5 sentences total. No bullet lists. No urgency scores.\n"
    "- If a life context is active, lead with it: 'You're in exam season with 4 exams in "
    "the next 10 days' or 'Recruiting is active — you have 3 interviews this week'. "
    "Let this color your tone and priorities.\n"
    "- If causal analysis or tradeoff context is available, weave it in naturally. "
    "Don't just warn — explain why something matters and what the recovery path looks like.\n"
    "- If TASK AFFECT data is available, weave it in naturally. Don't just list affects — "
    "use them to explain task order. 'Start with the project since you enjoy those — "
    "then tackle the essay you've been avoiding.'\n"
    "- Be conversational and direct, not formal."
)

QUERY_SYSTEM_PROMPT = (
    "You are a helpful academic deadline assistant. Answer the student's question "
    "based on their current state, which includes tasks, deadlines, Google Calendar "
    "events, file activity, behavioral patterns, identity profile, and email when relevant. "
    "If an 'ABOUT YOU' identity section is present, use it to personalize your answer. "
    "If RECENT EMAILS are included in the state, use them to answer email-related "
    "questions directly. Be concise and specific.\n\n"
    "IMPORTANT — use behavioral patterns prescriptively:\n"
    "- If the student asks when to work, recommend their primary productive window "
    "from the peak_hours patterns, not a generic time.\n"
    "- If they ask how long something will take, use effort_accuracy ratios to adjust "
    "the estimate (e.g., 'based on your past assignments, plan for ~3 hours, not 2').\n"
    "- If they ask what to prioritize, factor in procrastination patterns "
    "(e.g., 'you tend to start exams late — do that first').\n"
    "- Reference the data concretely: 'your most productive window is 7–9 PM' "
    "not 'consider working in the evening'.\n"
    "- If calendar events are listed in the state, use them to answer calendar questions.\n"
    "- If a life context is present (e.g., recruiting season, exam period), factor it into "
    "answers. During exam season, suggest study-focused scheduling. During recruiting, "
    "prioritize interview prep.\n"
    "- If TASK AFFECT labels are present, use them to personalize time management advice. "
    "For avoidance tasks, suggest micro-commitments. For anxiety tasks, suggest breaking "
    "them into smaller pieces. Reference the data: 'you typically start essays in the last "
    "10% of available time'."
)


def _log_training_data(session: Any, input_prompt: str, output_text: str, prompt_type: str, model: str) -> None:
    """Log LLM I/O for LoRA training data (opt-in via config)."""
    if not settings.lora_log_training_data:
        return
    try:
        from deadline_agent.models import DigestLog

        log_entry = DigestLog(
            input_prompt=input_prompt,
            output_text=output_text,
            model_used=str(model) if model else "unknown",
            prompt_type=prompt_type,
        )
        session.add(log_entry)
        session.commit()
    except Exception:
        logger.warning("Failed to log %s training data", prompt_type, exc_info=True)


class InsightOutput(BaseModel):
    """Schema for a single LLM-generated insight."""

    type: str  # no_progress | workload_spike | overdue_cluster | suggestion
    content: str
    related_task_ids: list[int]
    priority: int  # 1-5, 5 = most urgent


class InsightsResponse(BaseModel):
    """Schema for the full LLM reasoning response."""

    insights: list[InsightOutput]


async def _call_ollama(system: str, user: str, schema: dict[str, Any]) -> str:
    """Call Ollama with structured output. Raises ExtractionError on failure."""
    from ollama import AsyncClient

    model = settings.lora_reasoning_model or settings.reasoning_model or settings.extraction_model
    client = AsyncClient(host=settings.ollama_base_url)
    try:
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema,
        )
        return response.message.content or ""
    except Exception as e:
        raise ExtractionError(f"Ollama reasoning failed: {e}") from e


async def _call_anthropic(system: str, user: str, *, schema: dict[str, Any] | None = None) -> str:
    """Call Anthropic API for reasoning. Uses tool calling when schema is provided.

    Raises ExtractionError on failure.
    """
    from anthropic import AsyncAnthropic

    if not settings.anthropic_api_key:
        raise ExtractionError("Anthropic API key not configured")
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        if schema is not None:
            # Structured output via tool calling
            tool = {
                "name": "reasoning_output",
                "description": "Structured reasoning output",
                "input_schema": schema,
            }
            response = await client.messages.create(  # type: ignore[call-overload]
                model=settings.anthropic_model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "reasoning_output"},
            )
            for block in response.content:
                if block.type == "tool_use":
                    return json.dumps(block.input)
            raise ExtractionError("Anthropic returned no tool_use block")
        else:
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text  # type: ignore[union-attr]
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Anthropic reasoning failed: {e}") from e


def _use_anthropic_primary() -> bool:
    """Check if Anthropic should be the primary reasoning provider."""
    return settings.reasoning_provider == "anthropic"


async def call_llm(
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
) -> str:
    """General-purpose LLM call with provider routing and fallback.

    When *schema* is provided, the response is forced to match the JSON schema
    (via Ollama format or Anthropic tool calling).  Without a schema, the raw
    text response is returned.

    Raises ``ExtractionError`` if both providers fail.
    """
    if _use_anthropic_primary():
        try:
            return await _call_anthropic(system, user, schema=schema)
        except ExtractionError:
            return await _call_ollama(system, user, schema or {})
    else:
        try:
            return await _call_ollama(system, user, schema or {})
        except ExtractionError:
            if settings.use_anthropic_fallback and settings.anthropic_api_key:
                return await _call_anthropic(system, user, schema=schema)
            raise


_last_insight_snapshot_hash: str | None = None


def _reset_insight_cache() -> None:
    """Reset the insight staleness cache. Used in tests."""
    global _last_insight_snapshot_hash
    _last_insight_snapshot_hash = None


async def generate_insights(session: Any) -> list[Insight]:
    """Build state snapshot, call LLM, store insights.

    Skips LLM call if the state snapshot hasn't changed since the last run.
    Tries Ollama first, falls back to Anthropic. Returns empty list if both fail.
    """
    global _last_insight_snapshot_hash

    from deadline_agent.reasoning.cache import snapshot_hash

    snapshot = build_state_snapshot(session)
    prompt = snapshot.to_prompt()

    if not snapshot.due_today and not snapshot.due_this_week and not snapshot.overdue:
        logger.info("No upcoming tasks — skipping insight generation")
        return []

    # Skip if the state hasn't changed since last run
    current_hash = snapshot_hash(prompt)
    if current_hash == _last_insight_snapshot_hash:
        logger.info("State snapshot unchanged — skipping insight generation")
        return []
    _last_insight_snapshot_hash = current_hash

    # Route to primary provider, fall back to the other
    schema = InsightsResponse.model_json_schema()
    insights_data: list[InsightOutput] = []

    if _use_anthropic_primary():
        # Anthropic primary → Ollama fallback
        try:
            raw = await _call_anthropic(REASONING_SYSTEM_PROMPT, prompt, schema=schema)
            parsed = InsightsResponse.model_validate_json(raw)
            insights_data = parsed.insights
        except (ExtractionError, Exception) as e:
            logger.warning("Anthropic reasoning failed, trying Ollama: %s", e)
            try:
                raw = await _call_ollama(REASONING_SYSTEM_PROMPT, prompt, schema)
                parsed = InsightsResponse.model_validate_json(raw)
                insights_data = parsed.insights
            except (ExtractionError, Exception):
                logger.exception("Ollama fallback also failed")
                return []
    else:
        # Ollama primary → Anthropic fallback
        try:
            raw = await _call_ollama(REASONING_SYSTEM_PROMPT, prompt, schema)
            parsed = InsightsResponse.model_validate_json(raw)
            insights_data = parsed.insights
        except (ExtractionError, Exception) as e:
            logger.warning("Ollama reasoning failed, trying Anthropic: %s", e)
            if settings.use_anthropic_fallback and settings.anthropic_api_key:
                try:
                    raw = await _call_anthropic(REASONING_SYSTEM_PROMPT, prompt, schema=schema)
                    parsed = InsightsResponse.model_validate_json(raw)
                    insights_data = parsed.insights
                except (ExtractionError, Exception):
                    logger.exception("Anthropic reasoning also failed")
                    return []
            else:
                return []

    # Store insights
    repo = InsightRepository(session)
    repo.clear_stale(max_age_hours=24)

    stored: list[Insight] = []
    for item in insights_data[:5]:  # Cap at 5
        insight = repo.create(
            {
                "type": item.type,
                "content": item.content,
                "related_task_ids": json.dumps(item.related_task_ids),
            }
        )
        stored.append(insight)

    logger.info("Generated %d insights", len(stored))

    # Log insight I/O for LoRA training data
    model = settings.lora_reasoning_model or settings.reasoning_model or settings.extraction_model
    _log_training_data(session, prompt, raw, "insight", model)

    # Generate recovery plans for overdue tasks (only when overdue, not just unworked)
    has_overdue = any(i.type == "overdue_cluster" for i in insights_data)
    if has_overdue and snapshot.overdue:
        try:
            from deadline_agent.reasoning.recovery import generate_recovery_plans

            plans = await generate_recovery_plans(
                session, snapshot.overdue, [], snapshot.behavioral_patterns
            )
            for plan in plans[:3]:
                blocks = "; ".join(
                    f"{b.day} {b.time_window}: {b.action}" for b in plan.daily_blocks
                )
                content = (
                    f"Recovery plan for {plan.task_title} ({plan.status}): "
                    f"~{plan.estimated_hours_remaining:.1f}h remaining. "
                    f"{blocks}"
                )
                if plan.tradeoff_note:
                    content += f" Note: {plan.tradeoff_note}"
                recovery_insight = repo.create(
                    {
                        "type": "recovery_plan",
                        "content": content,
                        "related_task_ids": "[]",
                    }
                )
                stored.append(recovery_insight)
        except Exception:
            logger.exception("Recovery plan generation failed during insight cycle")

    return stored


async def generate_summary(session: Any) -> str | None:
    """Generate an insight-driven natural language digest.

    Uses the full unified context (calendar gaps, weekly stats) and
    injects any active insights so the LLM can synthesize rather than
    just list tasks.  Results are cached for 30 minutes.
    """
    from deadline_agent.reasoning.cache import digest_cache, snapshot_hash
    from deadline_agent.reasoning.state import build_unified_context
    from deadline_agent.store.insight_repository import InsightRepository

    ctx = await build_unified_context(session)

    if not ctx.due_today and not ctx.due_this_week and not ctx.overdue:
        return None

    prompt = ctx.to_prompt()

    # Inject active insights so the digest can reference them
    repo = InsightRepository(session)
    active_insights = repo.list_active(limit=5)
    if active_insights:
        insight_lines = ["\nACTIVE INSIGHTS (from reasoning engine):"]
        for ins in active_insights:
            insight_lines.append(f"  [{ins.type}] {ins.content}")
        prompt += "\n".join(insight_lines)

    # Return cached result if the underlying state hasn't changed
    cache_key = f"digest:{snapshot_hash(prompt)}"
    cached = digest_cache.get(cache_key)
    if cached is not None:
        logger.debug("Returning cached digest")
        return cached

    summary_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }

    def _extract_summary(raw: str) -> str:
        """Extract summary text from Ollama's JSON response."""
        try:
            return json.loads(raw).get("summary", raw)
        except (json.JSONDecodeError, AttributeError):
            return raw

    result: str | None = None
    if _use_anthropic_primary():
        try:
            result = await _call_anthropic(DIGEST_SYSTEM_PROMPT, prompt)
        except ExtractionError:
            try:
                raw = await _call_ollama(DIGEST_SYSTEM_PROMPT, prompt, summary_schema)
                result = _extract_summary(raw)
            except ExtractionError:
                pass
    else:
        try:
            raw = await _call_ollama(DIGEST_SYSTEM_PROMPT, prompt, summary_schema)
            result = _extract_summary(raw)
        except ExtractionError:
            if settings.use_anthropic_fallback and settings.anthropic_api_key:
                try:
                    result = await _call_anthropic(DIGEST_SYSTEM_PROMPT, prompt)
                except ExtractionError:
                    pass

    if result is not None:
        digest_cache.set(cache_key, result)
        # Log digest I/O for LoRA training data
        digest_model = settings.lora_reasoning_model or settings.reasoning_model or settings.extraction_model
        _log_training_data(session, prompt, result, "digest", digest_model)
    return result


async def answer_query(session: Any, question: str) -> str:
    """Answer a natural language question with full state context."""
    from pathlib import Path as _P
    _dbg = _P.home() / ".deadline-agent" / "calendar_debug.log"
    _dbg.write_text(f"question: {question!r}\n")
    snapshot = build_state_snapshot(session)

    # Fetch live Google Calendar events (next 7 days) so the LLM can answer
    # both "today" and "this week" calendar questions
    try:
        from datetime import timedelta

        from deadline_agent.reasoning.calendar_gaps import fetch_events
        from deadline_agent.reasoning.state import USER_TZ

        local_now = snapshot.now.astimezone(USER_TZ)
        sod = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        eow = sod + timedelta(days=7)
        snapshot.calendar_events = await fetch_events(sod.isoformat(), eow.isoformat())
        _dbg.write_text(_dbg.read_text() + f"fetched {len(snapshot.calendar_events)} events: {snapshot.calendar_events[:3]}\n")
    except Exception as exc:
        _dbg.write_text(_dbg.read_text() + f"fetch FAILED: {exc!r}\n")

    # If question is about email, fetch Gmail results and inject into context
    email_keywords = {"email", "emails", "inbox", "gmail", "mail", "message", "reply", "replies",
                      "recruiter", "offer", "rejection", "application status", "heard back"}
    q_lower = question.lower()
    if any(kw in q_lower for kw in email_keywords):
        try:
            import httpx

            from deadline_agent.auth import TokenManager

            tm = TokenManager()
            token = await tm.get_valid_token()
            if token:
                # Build a Gmail search query from the user's question
                gmail_query = "in:inbox newer_than:7d"
                # Add specific filters based on keywords
                if "recruiter" in q_lower or "job" in q_lower or "offer" in q_lower or "application" in q_lower:
                    gmail_query = "in:inbox newer_than:14d (recruiter OR interview OR application OR offer OR hiring)"
                elif "reply" in q_lower or "replies" in q_lower or "heard back" in q_lower:
                    gmail_query = "in:inbox newer_than:7d"
                elif "unread" in q_lower:
                    gmail_query = "is:unread in:inbox"

                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        params={"q": gmail_query, "maxResults": 10},
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        msg_ids = [m["id"] for m in resp.json().get("messages", [])]
                        email_lines = ["\nRECENT EMAILS (from Gmail):"]
                        for mid in msg_ids[:10]:
                            mr = await client.get(
                                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=10.0,
                            )
                            if mr.status_code == 200:
                                md = mr.json()
                                hdrs = {h["name"].lower(): h["value"] for h in md.get("payload", {}).get("headers", [])}
                                email_lines.append(
                                    f"  - From: {hdrs.get('from', '?')} | Subject: {hdrs.get('subject', '?')} | "
                                    f"Date: {hdrs.get('date', '?')} | Snippet: {md.get('snippet', '')[:100]}"
                                )
                        if len(email_lines) > 1:
                            snapshot._email_context = "\n".join(email_lines)
        except Exception:
            logger.warning("Gmail search for query context failed", exc_info=True)

    email_context = getattr(snapshot, "_email_context", "")
    user_msg = f"Student's current state:\n{snapshot.to_prompt()}\n{email_context}\n\nQuestion: {question}"
    answer_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    if _use_anthropic_primary():
        try:
            return await _call_anthropic(QUERY_SYSTEM_PROMPT, user_msg)
        except ExtractionError:
            try:
                return await _call_ollama(QUERY_SYSTEM_PROMPT, user_msg, answer_schema)
            except ExtractionError:
                pass
    else:
        try:
            return await _call_ollama(QUERY_SYSTEM_PROMPT, user_msg, answer_schema)
        except ExtractionError:
            if settings.use_anthropic_fallback and settings.anthropic_api_key:
                try:
                    return await _call_anthropic(QUERY_SYSTEM_PROMPT, user_msg)
                except ExtractionError:
                    pass
    return "Unable to generate a response — both Ollama and Anthropic are unavailable."
