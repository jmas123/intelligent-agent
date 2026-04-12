"""Phase 25: Recruiting intelligence analyzers.

Computes response rates, over-indexing, tier gaps, resume effectiveness,
temporal patterns, and fit scoring from RecruitingApplication data.
"""

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import BehavioralPattern, RecruitingApplication
from deadline_agent.store.pattern_repository import PatternRepository

logger = logging.getLogger(__name__)

# Known companies for heuristic tier inference.
# Matching is substring-based: "cisco" matches "cisco", "cisco ai", etc.
_TIER_HEURISTICS: dict[str, list[str]] = {
    "big_tech": [
        # FAANG+ / mega-cap tech
        "google", "meta", "facebook", "apple", "amazon", "microsoft",
        "netflix", "nvidia", "alphabet", "openai", "anthropic", "deepmind",
        "tesla", "uber", "lyft", "airbnb", "doordash", "instacart",
        "cisco", "intel", "amd", "qualcomm", "broadcom", "samsung",
        "tiktok", "bytedance", "snap", "pinterest", "spotify",
        "twitter", "x corp", "linkedin",
        "oracle", "ibm", "sap", "vmware",
        "zoom", "slack", "dropbox",
        "dell", "hp", "lenovo",
        "scale ai", "cohere", "cursor", "replit",
    ],
    "finance": [
        # Quant / trading / banking / fintech (finance-first)
        "jane street", "citadel", "two sigma", "de shaw", "point72",
        "bridgewater", "jump trading", "hudson river", "optiver",
        "drw", "imc", "sig", "susquehanna", "virtu", "tower research",
        "goldman sachs", "goldman", "jp morgan", "morgan stanley",
        "blackrock", "blackstone", "kkr", "carlyle",
        "barclays", "deutsche bank", "ubs", "credit suisse", "citi",
        "bank of america", "wells fargo", "capital one",
        "capco", "deloitte", "mckinsey", "bain", "bcg", "accenture",
        "aquatic", "aquatic capital",
    ],
    "mid_cap": [
        # Established tech / growth-stage with significant scale
        "salesforce", "adobe", "intuit", "servicenow",
        "snowflake", "datadog", "cloudflare", "twilio", "stripe",
        "square", "block", "plaid", "brex", "ramp", "robinhood",
        "coinbase", "palantir", "databricks", "figma",
        "rippling", "affirm", "flexport", "navan", "suno",
        "benchling", "glean", "attentive", "patreon",
        "airtable", "air table", "notion", "asana", "monday",
        "seat geek", "seatgeek", "stub hub", "stubhub",
        "nordstrom", "nike", "fanatics",
        "axon", "aurora", "sift", "yext",
        "twitch", "discord", "reddit",
        "voxel", "precisely", "ncr", "laerdal",
        "first american", "alloy", "color",
    ],
    "startup_growth": [
        # Series B+ startups with traction
        "scale", "cohere", "anyscale", "modal", "together ai",
        "distyl", "flex ai", "morg ai", "tog ai", "loop ai",
        "borderless", "veryable", "perpay", "nectar",
        "aircall", "baton", "ellipsis", "revivity",
        "gray swan", "gray swan ai", "trm",
        "wynd", "breeze", "marble", "karma", "rise",
        "automat", "galileo", "byte", "zip",
        "binance", "elise", "entain",
        "poke", "qode", "nord", "titan", "nue",
        "cobble stone", "exp", "easy", "rounds",
        "clear story",
    ],
    "startup_early": [
        # Seed / Series A — smaller, less established
        "absurd", "buford", "further", "harold",
        "angle", "black bird", "ren", "lat", "ssc",
        "loop fs", "flex zip", "colour",
    ],
}


def _confidence(n: int, threshold: int = 10) -> float:
    return min(1.0, n / threshold)


def classify_company_tier(company_normalized: str) -> str:
    """Classify a company into a tier using config map then heuristics."""
    # Config map takes priority
    if company_normalized in settings.company_tier_map:
        return settings.company_tier_map[company_normalized]

    if not settings.tier_inference_enabled:
        return "other"

    # Heuristic: check against known lists
    for tier, companies in _TIER_HEURISTICS.items():
        for known in companies:
            if known in company_normalized or company_normalized in known:
                return tier

    return "other"


async def _llm_classify_batch(companies: list[str]) -> dict[str, str]:
    """Use LLM to classify unknown companies into tiers. Returns {name: tier}."""
    if not companies:
        return {}

    from deadline_agent.reasoning.engine import _call_anthropic, _call_ollama

    prompt = (
        "Classify each company into exactly one tier. "
        "Tiers: big_tech (FAANG+, mega-cap), mid_cap (established tech, public/late-stage), "
        "startup_growth (Series B+, funded), startup_early (seed/Series A), "
        "finance (banks, trading firms, consulting), other (non-tech, unknown).\n\n"
        "Return JSON: {\"classifications\": [{\"company\": \"...\", \"tier\": \"...\"}]}\n\n"
        "Companies:\n" + "\n".join(f"- {c}" for c in companies)
    )

    schema = {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "tier": {"type": "string"},
                    },
                    "required": ["company", "tier"],
                },
            }
        },
        "required": ["classifications"],
    }

    valid_tiers = {"big_tech", "mid_cap", "startup_growth", "startup_early", "finance", "other"}
    result: dict[str, str] = {}

    try:
        import json as _json

        raw = await _call_ollama(
            "You classify companies into tiers. Return only valid JSON.",
            prompt,
            schema,
        )
        if raw:
            data = _json.loads(raw) if isinstance(raw, str) else raw
            for entry in data.get("classifications", []):
                tier = entry.get("tier", "other").lower()
                if tier in valid_tiers:
                    result[entry["company"].lower().strip()] = tier
    except Exception:
        # Try Anthropic fallback
        try:
            if settings.use_anthropic_fallback and settings.anthropic_api_key:
                raw = await _call_anthropic(
                    "You classify companies into tiers. Return only valid JSON.",
                    prompt,
                )
                if raw:
                    import json as _json2

                    data = _json2.loads(raw) if isinstance(raw, str) else raw
                    for entry in data.get("classifications", []):
                        tier = entry.get("tier", "other").lower()
                        if tier in valid_tiers:
                            result[entry["company"].lower().strip()] = tier
        except Exception:
            pass

    return result


def _ensure_tiers_with_llm(session: Session) -> None:
    """Backfill tiers: heuristics first, then LLM for unknowns."""
    import asyncio

    _ensure_tiers(session)

    # Find remaining unclassified (still "other") that might be classifiable
    stmt = select(RecruitingApplication).where(
        RecruitingApplication.company_tier == "other"
    )
    unknowns = list(session.scalars(stmt).all())
    if not unknowns:
        return

    # Batch LLM classification for unknowns
    company_names = [a.company_name for a in unknowns]
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — can't nest
            return
    except RuntimeError:
        pass

    try:
        classifications = asyncio.run(_llm_classify_batch(company_names))
        if classifications:
            updated = 0
            for app in unknowns:
                tier = classifications.get(app.company_normalized)
                if tier and tier != "other":
                    app.company_tier = tier
                    updated += 1
            if updated:
                session.commit()
                logger.info("LLM classified %d/%d unknown companies", updated, len(unknowns))
    except Exception:
        logger.debug("LLM tier classification unavailable, using heuristics only")


def _ensure_tiers(session: Session) -> None:
    """Backfill company_tier on applications that don't have one yet."""
    stmt = select(RecruitingApplication).where(
        RecruitingApplication.company_tier.is_(None)
    )
    apps = list(session.scalars(stmt).all())
    for app in apps:
        app.company_tier = classify_company_tier(app.company_normalized)
    if apps:
        session.commit()
        logger.info("Backfilled tier for %d applications", len(apps))


def _has_response(app: RecruitingApplication) -> bool:
    """Check if application advanced beyond 'applied'."""
    return app.status in ("response", "interview", "offer")


def analyze_response_rates(session: Session) -> list[BehavioralPattern]:
    """Compute application-to-response conversion rates by tier, role_type, and method."""
    repo = PatternRepository(session)
    _ensure_tiers_with_llm(session)

    all_apps = list(session.scalars(select(RecruitingApplication)).all())
    if not all_apps:
        return []

    patterns: list[BehavioralPattern] = []

    # Overall response rate
    non_closed = [a for a in all_apps if a.status != "closed" or _has_response(a)]
    total = len(all_apps)
    responded = sum(1 for a in all_apps if _has_response(a))
    interviewed = sum(1 for a in all_apps if a.status in ("interview", "offer"))
    rate = responded / total if total else 0

    p = repo.upsert(
        pattern_type="recruiting_response_rate",
        pattern_key="overall",
        value=json.dumps({
            "total": total,
            "responded": responded,
            "interviewed": interviewed,
            "rate": round(rate, 3),
        }),
        sample_count=total,
        confidence=_confidence(total, threshold=5),
    )
    patterns.append(p)

    # By tier
    by_tier: dict[str, list[RecruitingApplication]] = defaultdict(list)
    for app in all_apps:
        by_tier[app.company_tier or "other"].append(app)

    for tier, apps in by_tier.items():
        t = len(apps)
        r = sum(1 for a in apps if _has_response(a))
        iv = sum(1 for a in apps if a.status in ("interview", "offer"))
        p = repo.upsert(
            pattern_type="recruiting_response_rate",
            pattern_key=f"tier:{tier}",
            value=json.dumps({
                "total": t, "responded": r, "interviewed": iv,
                "rate": round(r / t, 3) if t else 0,
            }),
            sample_count=t,
            confidence=_confidence(t),
        )
        patterns.append(p)

    # By application method
    by_method: dict[str, list[RecruitingApplication]] = defaultdict(list)
    for app in all_apps:
        by_method[app.application_method or "unknown"].append(app)

    for method, apps in by_method.items():
        if method == "unknown":
            continue
        t = len(apps)
        r = sum(1 for a in apps if _has_response(a))
        p = repo.upsert(
            pattern_type="recruiting_response_rate",
            pattern_key=f"method:{method}",
            value=json.dumps({
                "total": t, "responded": r,
                "rate": round(r / t, 3) if t else 0,
            }),
            sample_count=t,
            confidence=_confidence(t),
        )
        patterns.append(p)

    return patterns


def analyze_over_indexing(session: Session) -> list[BehavioralPattern]:
    """Flag when 80%+ of applications target the same tier/sector."""
    repo = PatternRepository(session)
    _ensure_tiers(session)

    all_apps = list(session.scalars(select(RecruitingApplication)).all())
    total = len(all_apps)
    if total < 5:
        return []

    patterns: list[BehavioralPattern] = []

    # Check tier concentration
    by_tier: dict[str, list[RecruitingApplication]] = defaultdict(list)
    for app in all_apps:
        by_tier[app.company_tier or "other"].append(app)

    for tier, apps in by_tier.items():
        pct = len(apps) / total
        if pct >= 0.80:
            # Compute response rate for dominant vs others
            dominant_rate = (
                sum(1 for a in apps if _has_response(a)) / len(apps)
                if apps else 0
            )
            others = [a for a in all_apps if (a.company_tier or "other") != tier]
            other_rate = (
                sum(1 for a in others if _has_response(a)) / len(others)
                if others else 0
            )
            p = repo.upsert(
                pattern_type="recruiting_over_index",
                pattern_key=f"tier:{tier}",
                value=json.dumps({
                    "dominant": tier,
                    "pct": round(pct * 100, 1),
                    "total": total,
                    "count": len(apps),
                    "response_rate_dominant": round(dominant_rate, 3),
                    "response_rate_others": round(other_rate, 3),
                }),
                sample_count=total,
                confidence=_confidence(total, threshold=15),
            )
            patterns.append(p)

    return patterns


def analyze_tier_gaps(session: Session) -> list[BehavioralPattern]:
    """Identify missing or underweight tiers in the application portfolio."""
    repo = PatternRepository(session)
    _ensure_tiers(session)

    all_apps = list(session.scalars(select(RecruitingApplication)).all())
    total = len(all_apps)
    if total < 5:
        return []

    patterns: list[BehavioralPattern] = []
    known_tiers = {"big_tech", "mid_cap", "startup_early", "startup_growth", "finance"}

    by_tier: dict[str, int] = defaultdict(int)
    for app in all_apps:
        by_tier[app.company_tier or "other"] += 1

    for tier in known_tiers:
        count = by_tier.get(tier, 0)
        pct = count / total if total else 0
        if count == 0 or pct < 0.05:
            p = repo.upsert(
                pattern_type="recruiting_tier_gap",
                pattern_key=tier,
                value=json.dumps({
                    "count": count,
                    "pct": round(pct * 100, 1),
                    "total": total,
                }),
                sample_count=total,
                confidence=_confidence(total, threshold=10),
            )
            patterns.append(p)

    return patterns


def analyze_resume_effectiveness(session: Session) -> list[BehavioralPattern]:
    """Compare response rates across resume variants."""
    repo = PatternRepository(session)

    stmt = select(RecruitingApplication).where(
        RecruitingApplication.resume_variant.isnot(None)
    )
    apps_with_variant = list(session.scalars(stmt).all())

    # Need at least 2 variants to compare
    by_variant: dict[str, list[RecruitingApplication]] = defaultdict(list)
    for app in apps_with_variant:
        by_variant[app.resume_variant].append(app)

    if len(by_variant) < 2:
        return []

    patterns: list[BehavioralPattern] = []
    for variant, apps in by_variant.items():
        t = len(apps)
        r = sum(1 for a in apps if _has_response(a))
        rate = r / t if t else 0
        p = repo.upsert(
            pattern_type="recruiting_resume_effectiveness",
            pattern_key=variant,
            value=json.dumps({
                "total": t, "responded": r,
                "rate": round(rate, 3),
            }),
            sample_count=t,
            confidence=_confidence(t, threshold=5),
        )
        patterns.append(p)

    return patterns


def analyze_temporal_patterns(session: Session) -> list[BehavioralPattern]:
    """Compute response rates by day-of-week and hour of application."""
    repo = PatternRepository(session)

    all_apps = list(session.scalars(select(RecruitingApplication)).all())
    if len(all_apps) < 20:
        return []

    patterns: list[BehavioralPattern] = []
    _DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # By day of week
    by_day: dict[int, list[RecruitingApplication]] = defaultdict(list)
    for app in all_apps:
        if app.applied_day_of_week is not None:
            by_day[app.applied_day_of_week].append(app)

    for day, apps in by_day.items():
        t = len(apps)
        r = sum(1 for a in apps if _has_response(a))
        rate = r / t if t else 0
        p = repo.upsert(
            pattern_type="recruiting_temporal",
            pattern_key=f"day:{day}",
            value=json.dumps({
                "day_name": _DAY_NAMES[day] if day < 7 else str(day),
                "total": t, "responded": r,
                "rate": round(rate, 3),
            }),
            sample_count=t,
            confidence=_confidence(t, threshold=5),
        )
        patterns.append(p)

    # By hour bucket (morning/afternoon/evening/night)
    _HOUR_BUCKETS = {
        "morning": range(6, 12),
        "afternoon": range(12, 17),
        "evening": range(17, 22),
        "night": list(range(22, 24)) + list(range(0, 6)),
    }
    by_bucket: dict[str, list[RecruitingApplication]] = defaultdict(list)
    for app in all_apps:
        if app.applied_hour is not None:
            for bucket, hours in _HOUR_BUCKETS.items():
                if app.applied_hour in hours:
                    by_bucket[bucket].append(app)
                    break

    for bucket, apps in by_bucket.items():
        t = len(apps)
        r = sum(1 for a in apps if _has_response(a))
        rate = r / t if t else 0
        p = repo.upsert(
            pattern_type="recruiting_temporal",
            pattern_key=f"bucket:{bucket}",
            value=json.dumps({
                "bucket": bucket, "total": t, "responded": r,
                "rate": round(rate, 3),
            }),
            sample_count=t,
            confidence=_confidence(t, threshold=5),
        )
        patterns.append(p)

    return patterns


def analyze_fit_scoring(session: Session) -> list[BehavioralPattern]:
    """Rank active applications by likelihood of response.

    Multi-signal scoring:
    - Tier response rate: how well does this tier convert historically?
    - Recency: recent signals suggest the app is still alive
    - Staleness penalty: apps silent for 14+ days are likely dead
    - Signal density: more signals = more engagement from the company
    """
    repo = PatternRepository(session)
    now = datetime.now(UTC)

    all_apps = list(session.scalars(select(RecruitingApplication)).all())
    active = [a for a in all_apps if a.status not in ("closed", "offer")]

    if not active:
        return []

    # Compute tier response rates
    tier_rates: dict[str, float] = {}
    tier_counts: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    for app in all_apps:
        tier = app.company_tier or "other"
        total, responded = tier_counts[tier]
        tier_counts[tier] = (total + 1, responded + (1 if _has_response(app) else 0))
    for tier, (total, responded) in tier_counts.items():
        tier_rates[tier] = responded / total if total else 0

    # Score each active application
    scored: list[tuple[RecruitingApplication, float, list[str]]] = []

    for app in active:
        factors: list[str] = []
        score = 0.0

        # 1. Tier response rate (0-40 points)
        tier = app.company_tier or "other"
        tier_rate = tier_rates.get(tier, 0)
        tier_score = tier_rate * 40
        score += tier_score
        if tier_rate > 0:
            factors.append(f"{tier} tier ({tier_rate:.0%} response rate)")

        # 2. Status advancement (0-30 points)
        status_scores = {"applied": 0, "response": 20, "interview": 30}
        status_score = status_scores.get(app.status, 0)
        score += status_score
        if status_score > 0:
            factors.append(f"status: {app.status}")

        # 3. Signal density (0-15 points) — more signals = more company engagement
        try:
            signals = json.loads(app.signals_json)
            signal_count = len(signals)
        except (json.JSONDecodeError, TypeError):
            signal_count = 0
        signal_score = min(15, signal_count * 3)
        score += signal_score
        if signal_count >= 3:
            factors.append(f"{signal_count} signals")

        # 4. Recency bonus / staleness penalty (-15 to +15 points)
        if app.last_signal_at:
            signal_dt = app.last_signal_at
            if signal_dt.tzinfo is None:
                signal_dt = signal_dt.replace(tzinfo=UTC)
            days_since = (now - signal_dt).days
            if days_since <= 3:
                score += 15
                factors.append("active (signal <3d)")
            elif days_since <= 7:
                score += 8
            elif days_since <= 14:
                score += 0
            elif days_since <= 30:
                score -= 10
                factors.append(f"stale ({days_since}d)")
            else:
                score -= 15
                factors.append(f"likely dead ({days_since}d)")

        scored.append((app, max(0, score), factors))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Normalize to 0-1 range
    max_score = scored[0][1] if scored else 1
    if max_score == 0:
        max_score = 1

    patterns: list[BehavioralPattern] = []
    for rank, (app, raw_score, factors) in enumerate(scored, 1):
        normalized = round(raw_score / max_score, 3)
        p = repo.upsert(
            pattern_type="recruiting_fit_score",
            pattern_key=app.company_normalized,
            value=json.dumps({
                "company": app.company_name,
                "score": normalized,
                "rank": rank,
                "matching_factors": factors,
                "status": app.status,
                "raw_score": round(raw_score, 1),
            }),
            sample_count=len(all_apps),
            confidence=_confidence(len(all_apps), threshold=10),
        )
        patterns.append(p)

    return patterns


def run_recruiting_analyses(session: Session) -> list[BehavioralPattern]:
    """Run all recruiting analytics. Returns all updated patterns."""
    all_patterns: list[BehavioralPattern] = []

    for fn in [
        analyze_response_rates,
        analyze_over_indexing,
        analyze_tier_gaps,
        analyze_resume_effectiveness,
        analyze_temporal_patterns,
        analyze_fit_scoring,
    ]:
        try:
            patterns = fn(session)
            all_patterns.extend(patterns)
        except Exception:
            logger.exception("Recruiting analysis failed: %s", fn.__name__)

    logger.info("Recruiting analysis complete: %d pattern(s)", len(all_patterns))
    return all_patterns
