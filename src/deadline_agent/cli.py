"""CLI for viewing and managing extracted tasks."""

import asyncio
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import click


def _format_due(iso_str: str | None) -> str:
    """Format an ISO 8601 date string into a readable format."""
    if not iso_str:
        return "no due date"
    try:
        dt = datetime.fromisoformat(iso_str)
        dt = dt.astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%b %d, %Y %I:%M %p %Z")
    except ValueError:
        return iso_str


from deadline_agent.store.repository import TaskRepository
from deadline_agent.store.session import SessionLocal, init_db


@click.group()
def cli() -> None:
    """Deadline Agent — view and manage extracted tasks."""
    init_db()


@cli.command("list")
@click.option("--status", type=click.Choice(["pending", "done", "dismissed"]), default=None)
@click.option("--source", type=click.Choice(["gmail", "moodle", "gcal", "slack", "notion"]))
@click.option("--limit", default=20, show_default=True)
@click.option("--due-today", is_flag=True, help="Show only tasks due today")
def list_tasks(status: str | None, source: str | None, limit: int, due_today: bool) -> None:
    """List extracted tasks."""
    with SessionLocal() as session:
        repo = TaskRepository(session)
        tasks = repo.list_tasks(status=status, source=source, limit=limit)

    if due_today:
        est = ZoneInfo("America/New_York")
        today = datetime.now(est).date()
        tasks = [
            t
            for t in tasks
            if t.due_date_iso
            and datetime.fromisoformat(t.due_date_iso).astimezone(est).date() == today
        ]

    if not tasks:
        click.echo("No tasks found.")
        return

    for t in tasks:
        urgency = "!" * t.urgency_score
        due = _format_due(t.due_date_iso)
        click.echo(f"[{t.id}] {urgency} {t.title} ({t.source}) — {due} [{t.status}]")


@cli.command()
@click.argument("task_id", type=int)
def show(task_id: int) -> None:
    """Show details of a specific task."""
    with SessionLocal() as session:
        repo = TaskRepository(session)
        task = repo.get_task(task_id)

    if task is None:
        click.echo(f"Task {task_id} not found.")
        return

    click.echo(f"ID:       {task.id}")
    click.echo(f"Title:    {task.title}")
    click.echo(f"Due:      {_format_due(task.due_date_iso)}")
    click.echo(f"Source:   {task.source}")
    click.echo(f"Type:     {task.type}")
    click.echo(f"Course:   {task.course or 'none'}")
    click.echo(f"Urgency:  {'!' * task.urgency_score} ({task.urgency_score}/5)")
    click.echo(f"Status:   {task.status}")


@cli.command()
@click.argument("task_id", type=int)
@click.argument("new_status", type=click.Choice(["done", "dismissed", "pending"]))
def mark(task_id: int, new_status: str) -> None:
    """Update a task's status."""
    with SessionLocal() as session:
        repo = TaskRepository(session)
        task = repo.update_status(task_id, new_status)

    if task is None:
        click.echo(f"Task {task_id} not found.")
        return

    click.echo(f"Task {task_id} marked as {new_status}.")


@cli.command("add")
@click.argument("title")
@click.option("--due", default=None, help="Due date (ISO 8601, e.g. 2026-05-01 or 2026-05-01T23:59)")
@click.option("--type", "task_type", default="personal",
              type=click.Choice(["assignment", "exam", "meeting", "personal", "project", "interview_prep"]),
              show_default=True)
@click.option("--course", default=None, help="Course or project name")
@click.option("--urgency", default=3, type=click.IntRange(1, 5), show_default=True, help="1-5")
def add_task(title: str, due: str | None, task_type: str, course: str | None, urgency: int) -> None:
    """Manually create a task."""
    import hashlib

    raw_hash = hashlib.sha256(
        f"manual:{title}:{due or ''}:{task_type}:{datetime.now().isoformat()}".encode()
    ).hexdigest()

    # Normalize date-only to end-of-day timestamp
    due_iso = due
    if due_iso and "T" not in due_iso:
        due_iso = f"{due_iso}T23:59:00+00:00"

    with SessionLocal() as session:
        repo = TaskRepository(session)
        task = repo.create_task(
            {
                "title": title,
                "due_date_iso": due_iso,
                "source": "manual",
                "type": task_type,
                "course": course,
                "urgency_score": urgency,
                "confidence": 1.0,
                "raw_hash": raw_hash,
            }
        )
    if task is None:
        click.echo("Task already exists (duplicate).")
        return
    click.echo(f"Created task [{task.id}] {task.title} ({task_type})" +
               (f" — due {_format_due(due)}" if due else ""))


@cli.command("fetch-moodle")
@click.argument("urls", nargs=-1, required=True)
def fetch_moodle(urls: tuple[str, ...]) -> None:
    """Fetch Moodle iCal feeds and process through the pipeline."""
    from deadline_agent.ingestion.moodle import MoodleIngester
    from deadline_agent.pipeline import drain_queue

    async def _fetch_and_process() -> tuple[int, int]:
        ingester = MoodleIngester(feed_urls=list(urls))
        enqueued = await ingester.fetch_and_enqueue()
        stored = await drain_queue(SessionLocal)
        return enqueued, stored

    enqueued, stored = asyncio.run(_fetch_and_process())
    click.echo(f"Fetched {enqueued} events, stored {stored} tasks.")


@cli.command()
def digest() -> None:
    """Show the morning digest of upcoming deadlines."""
    from deadline_agent.notifications.digest import generate_digest

    with SessionLocal() as session:
        text = generate_digest(session)
    if text is None:
        click.echo("No upcoming tasks.")
    else:
        click.echo(text)


@cli.group()
def alerts() -> None:
    """Manage pre-deadline alerts."""


@alerts.command("check")
def alerts_check() -> None:
    """Run a pre-deadline alert check now."""
    from deadline_agent.notifications.alerts import check_and_send_alerts
    from deadline_agent.reasoning.intervention_engine import init_intervention_engine

    # Ensure intervention engine is initialized for CLI context
    try:
        from deadline_agent.reasoning.intervention_engine import get_intervention_engine

        get_intervention_engine()
    except RuntimeError:
        init_intervention_engine(SessionLocal)

    with SessionLocal() as session:
        count = asyncio.run(check_and_send_alerts(session))
    click.echo(f"Sent {count} alert(s).")


@cli.command()
@click.argument("message")
def notify(message: str) -> None:
    """Send a test macOS notification."""
    from deadline_agent.notifications.macos import send_notification

    if send_notification("Deadline Agent", message):
        click.echo("Notification sent.")
    else:
        click.echo("Failed to send notification.", err=True)


@cli.group("config")
def config_group() -> None:
    """Manage configuration."""


@config_group.command("init")
def config_init() -> None:
    """Create a template config file at ~/.deadline-agent/config.toml."""
    from deadline_agent.config_loader import DEFAULT_CONFIG_PATH
    from deadline_agent.config_template import generate_template

    if DEFAULT_CONFIG_PATH.exists():
        click.confirm(f"{DEFAULT_CONFIG_PATH} already exists. Overwrite?", abort=True)

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(generate_template())
    click.echo(f"Config template written to {DEFAULT_CONFIG_PATH}")


@config_group.command("show")
def config_show() -> None:
    """Show current effective configuration."""
    from deadline_agent.config import settings

    for field_name in settings.model_fields:
        value = getattr(settings, field_name)
        if "key" in field_name and value:
            # Mask sensitive values
            value = str(value)[:4] + "..." if len(str(value)) > 4 else "****"
        click.echo(f"{field_name} = {value!r}")


@cli.group("activity")
def activity_group() -> None:
    """View file activity and linked deadlines."""


@activity_group.command("list")
@click.option("--hours", default=72, show_default=True, help="Show activity from last N hours")
@click.option("--task-id", type=int, default=None, help="Filter by linked task ID")
def activity_list(hours: int, task_id: int | None) -> None:
    """Show recent file activity."""
    from deadline_agent.store.file_repository import FileActivityRepository

    with SessionLocal() as session:
        repo = FileActivityRepository(session)
        if task_id is not None:
            activities = repo.get_activity_for_task(task_id)
        else:
            activities = repo.get_recent_activity(hours=hours)

    if not activities:
        click.echo("No file activity found.")
        return

    for a in activities:
        ts = a.modified_at.strftime("%b %d %H:%M") if a.modified_at else "?"
        click.echo(f"  [{a.event_type:8s}] {ts}  {a.path}")


@activity_group.command("links")
def activity_links() -> None:
    """Show file-to-task links for upcoming deadlines."""
    from deadline_agent.store.file_repository import FileActivityRepository

    with SessionLocal() as session:
        repo = TaskRepository(session)
        file_repo = FileActivityRepository(session)
        tasks = repo.list_tasks(status="pending")

    if not tasks:
        click.echo("No pending tasks.")
        return

    for t in tasks:
        due = _format_due(t.due_date_iso)
        with SessionLocal() as session:
            file_repo = FileActivityRepository(session)
            linked = file_repo.get_activity_for_task(t.id)

        if linked:
            click.echo(f"[{t.id}] {t.title} — {due}")
            for a in linked[:5]:
                click.echo(f"      {a.filename} ({a.event_type}, {a.directory})")
        else:
            click.echo(f"[{t.id}] {t.title} — {due}  ⚠ NO FILES LINKED")


@cli.command("status")
def status_command() -> None:
    """Show a natural language summary of your current state."""
    from deadline_agent.reasoning.engine import generate_summary
    from deadline_agent.reasoning.state import build_state_snapshot

    async def _run() -> str | None:
        with SessionLocal() as session:
            return await generate_summary(session)

    result = asyncio.run(_run())
    if result:
        click.echo(result)
    else:
        # Fallback to plain text snapshot
        with SessionLocal() as session:
            snapshot = build_state_snapshot(session)
        click.echo(snapshot.to_prompt())


@cli.group("insights")
def insights_group() -> None:
    """View and manage LLM-generated insights."""


@insights_group.command("list")
def insights_list() -> None:
    """Show active insights."""
    from deadline_agent.store.insight_repository import InsightRepository

    with SessionLocal() as session:
        repo = InsightRepository(session)
        insights = repo.list_active(limit=10)

    if not insights:
        click.echo("No active insights.")
        return

    for i in insights:
        click.echo(f"[{i.id}] ({i.type}) {i.content}")


@insights_group.command("dismiss")
@click.argument("insight_id", type=int)
def insights_dismiss(insight_id: int) -> None:
    """Dismiss an insight."""
    from deadline_agent.store.insight_repository import InsightRepository

    with SessionLocal() as session:
        repo = InsightRepository(session)
        result = repo.dismiss(insight_id)

    if result is None:
        click.echo(f"Insight {insight_id} not found.")
    else:
        click.echo(f"Insight {insight_id} dismissed.")


@insights_group.command("generate")
def insights_generate() -> None:
    """Generate insights now."""
    from deadline_agent.reasoning.engine import generate_insights

    async def _run() -> int:
        with SessionLocal() as session:
            insights = await generate_insights(session)
            return len(insights)

    count = asyncio.run(_run())
    click.echo(f"Generated {count} insight(s).")


@cli.command("ask")
@click.argument("question")
def ask_command(question: str) -> None:
    """Ask a question about your deadlines and workload."""
    from deadline_agent.reasoning.engine import answer_query

    async def _run() -> str:
        with SessionLocal() as session:
            return await answer_query(session, question)

    answer = asyncio.run(_run())
    click.echo(answer)


@cli.command("simulate")
@click.argument("scenario")
def simulate_command(scenario: str) -> None:
    """Simulate a what-if scenario over your behavioral data."""
    from deadline_agent.reasoning.simulation import simulate_scenario

    async def _run() -> object:
        with SessionLocal() as session:
            return await simulate_scenario(session, scenario)

    try:
        result = asyncio.run(_run())
    except Exception as e:
        click.echo(f"Simulation failed: {e}", err=True)
        raise SystemExit(1) from e

    # Confidence badge
    conf_icon = {"high": "+", "medium": "~", "low": "-"}.get(result.confidence, "?")
    click.echo(f"[{conf_icon}] Confidence: {result.confidence} — {result.confidence_reason}\n")

    # Projected impacts
    if result.projected_impacts:
        click.echo("PROJECTED IMPACTS:")
        severity_icons = {
            "positive": "+", "neutral": " ", "concerning": "!", "critical": "!!",
        }
        for impact in result.projected_impacts:
            icon = severity_icons.get(impact["severity"], "?")
            click.echo(f"  [{icon}] {impact['domain']}: {impact['impact']}")
        click.echo()

    # Weekly projection
    if result.weekly_projection:
        click.echo("WEEK-BY-WEEK PROJECTION:")
        click.echo(result.weekly_projection)
        click.echo()

    # Recommendation
    if result.recommendation:
        click.echo("RECOMMENDATION:")
        click.echo(result.recommendation)

    # Data density
    click.echo(f"\nData: {result.data_density}")


@cli.command("chat")
def chat_command() -> None:
    """Open the floating widget interface."""
    from deadline_agent.ui.widget.window import run_widget

    run_widget()


@cli.command("widget")
def widget_command() -> None:
    """Launch the floating desktop widget."""
    from deadline_agent.ui.widget.window import run_widget

    run_widget()


@cli.command("weekly-review")
def weekly_review_command() -> None:
    """Show a weekly review of your task activity."""
    from deadline_agent.reasoning.weekly_review import generate_weekly_review

    async def _run() -> str:
        with SessionLocal() as session:
            return await generate_weekly_review(session)

    review = asyncio.run(_run())
    click.echo(review)


@cli.command("analyze")
def analyze_command() -> None:
    """Run behavioral analysis now (infer sessions + detect patterns)."""
    from deadline_agent.behavioral.analyzer import run_all_analyses
    from deadline_agent.behavioral.session_inference import infer_sessions

    with SessionLocal() as session:
        work_sessions = infer_sessions(session)
        click.echo(f"Inferred {len(work_sessions)} new work session(s).")
        patterns = run_all_analyses(session)
        click.echo(f"Updated {len(patterns)} behavioral pattern(s).")

    if patterns:
        click.echo("\nRun `deadline-agent patterns` to see results.")


@cli.command("retrolink")
def retrolink_command() -> None:
    """Link existing file activity to tasks and classify life tracks."""
    from deadline_agent.awareness.classifier import classify_file
    from deadline_agent.awareness.linker import TaskLinker
    from deadline_agent.store.file_repository import FileActivityRepository

    with SessionLocal() as session:
        file_repo = FileActivityRepository(session)
        linker = TaskLinker(session)

        activities = file_repo.get_recent_activity(hours=720)  # last 30 days
        click.echo(f"Scanning {len(activities)} file activities...")

        linked = 0
        classified = 0
        for activity in activities:
            # Classify life track
            if activity.life_track is None:
                track = classify_file(activity.path, activity.filename, activity.directory)
                if track is not None:
                    activity.life_track = track
                    classified += 1

            # Link to tasks
            matches = linker.find_matching_tasks(activity)
            for task, confidence, method in matches:
                result = file_repo.link_to_task(
                    activity.id, task.id, confidence, method
                )
                if result is not None:
                    linked += 1

        session.commit()
        click.echo(f"Created {linked} new file-task link(s).")
        click.echo(f"Classified {classified} file(s) by life track.")
        if linked > 0 or classified > 0:
            click.echo("Run `deadline-agent analyze` to update patterns.")


@cli.group("context")
def context_group() -> None:
    """View and manage life contexts (seasons)."""


@context_group.command("show")
def context_show() -> None:
    """Show active life contexts."""
    from deadline_agent.store.context_repository import LifeContextRepository

    with SessionLocal() as session:
        repo = LifeContextRepository(session)
        contexts = repo.get_active()

        if not contexts:
            click.echo("No active life contexts.")
            click.echo("Use `deadline-agent context set` to define one, or run `deadline-agent context detect`.")
            return

        for ctx in contexts:
            label = f" — {ctx.label}" if ctx.label else ""
            source_tag = "(user-set)" if ctx.source == "manual" else "(auto-detected)"
            click.echo(f"[{ctx.id}] {ctx.season.upper()}{label}: {ctx.start_date} to {ctx.end_date} {source_tag}")


@context_group.command("set")
@click.argument("season", type=click.Choice(["recruiting", "exams", "light_week", "default"]))
@click.option("--start", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--end", required=True, help="End date (YYYY-MM-DD)")
@click.option("--label", default=None, help="Optional human-friendly label")
def context_set(season: str, start: str, end: str, label: str | None) -> None:
    """Set a manual life context."""
    from datetime import date as _date

    # Validate dates
    try:
        start_d = _date.fromisoformat(start)
        end_d = _date.fromisoformat(end)
    except ValueError:
        click.echo("Invalid date format. Use YYYY-MM-DD.", err=True)
        return

    if end_d < start_d:
        click.echo("End date must be on or after start date.", err=True)
        return

    from deadline_agent.store.context_repository import LifeContextRepository

    with SessionLocal() as session:
        repo = LifeContextRepository(session)
        ctx = repo.set_manual(season, start, end, label)
        click.echo(f"Life context set: [{ctx.id}] {ctx.season.upper()} ({start} to {end})")


@context_group.command("clear")
@click.argument("context_id", type=int)
def context_clear(context_id: int) -> None:
    """Deactivate a life context."""
    from deadline_agent.store.context_repository import LifeContextRepository

    with SessionLocal() as session:
        repo = LifeContextRepository(session)
        result = repo.deactivate(context_id)
        if result is None:
            click.echo(f"Life context {context_id} not found.")
        else:
            click.echo(f"Life context {context_id} ({result.season}) deactivated.")


@context_group.command("detect")
def context_detect() -> None:
    """Run auto-detection heuristics now."""
    from deadline_agent.awareness.life_context_detector import detect_life_contexts

    with SessionLocal() as session:
        contexts = detect_life_contexts(session)
        if not contexts:
            click.echo("No life contexts auto-detected.")
        else:
            click.echo(f"Auto-detected {len(contexts)} life context(s):")
            for ctx in contexts:
                label = f" — {ctx.label}" if ctx.label else ""
                click.echo(f"  [{ctx.id}] {ctx.season.upper()}{label}: {ctx.start_date} to {ctx.end_date}")


@cli.command("risk")
@click.option("--recompute", is_flag=True, help="Force recompute before displaying")
def risk_command(recompute: bool) -> None:
    """Show per-task risk of missing deadlines."""
    from deadline_agent.reasoning.risk_engine import compute_task_risks, get_current_risks

    if recompute:
        click.echo("Recomputing risk assessments...")

        async def _recompute() -> None:
            with SessionLocal() as session:
                await compute_task_risks(session)

        asyncio.run(_recompute())

    with SessionLocal() as session:
        items = get_current_risks(session)

    if not items:
        click.echo("No risk assessments computed yet.")
        click.echo("Run with --recompute to generate assessments, or wait for the next reasoning cycle.")
        return

    LEVEL_ICON = {"critical": "!!!", "high": "!! ", "medium": "!  ", "low": "   "}

    click.echo()
    for item in items:
        icon = LEVEL_ICON.get(item["risk_level"], "   ")
        pct = item["probability_pct"]
        title = item["title"]
        course = f" ({item['course']})" if item.get("course") else ""
        time_label = item["time_label"]

        click.echo(f"  {icon} {pct}%  {title}{course}")
        click.echo(f"         {time_label} | {item['hours_needed']:.1f}h needed | {item['hours_available']:.1f}h available | {item['hours_worked']:.1f}h worked")
        click.echo(f"         {item['summary']}")
        if item["confidence"] < 0.5:
            click.echo(f"         ⚠ {item['confidence_reason']}")
        click.echo()


@cli.command("priorities")
@click.option("--recompute", is_flag=True, help="Force recompute before displaying")
@click.option("--limit", default=5, show_default=True, help="Number of priorities to show")
def priorities_command(recompute: bool, limit: int) -> None:
    """Show ranked priorities — what you should do right now."""
    from deadline_agent.reasoning.priority_engine import (
        compute_priorities,
        get_current_priorities,
    )

    if recompute:
        click.echo("Recomputing priorities...")

        async def _recompute() -> None:
            with SessionLocal() as session:
                await compute_priorities(session)

        asyncio.run(_recompute())

    with SessionLocal() as session:
        items = get_current_priorities(session, limit=limit)

    if not items:
        click.echo("No priorities computed yet.")
        click.echo("Run with --recompute to generate priorities, or wait for the next reasoning cycle.")
        return

    click.echo()
    for item in items:
        rank = item["rank"]
        title = item["title"]
        course = f" ({item['course']})" if item.get("course") else ""
        time_label = item["time_label"]
        score = item["score"]

        # Header line
        click.echo(f"  #{rank}  {title}{course}")
        click.echo(f"      {time_label} | score: {score:.2f}")
        click.echo(f"      Risk: {item['risk_if_ignored']}")
        click.echo(f"      Action: {item['suggested_action']}")
        click.echo(f"      Why: {item['reasoning']}")
        click.echo()


@cli.command("start")
@click.option("--life-track", default=None, help="Optional life_track hint (school|recruiting|project)")
@click.option("--compact", is_flag=True, help="One-line briefing (menubar/voice style)")
def start_command(life_track: str | None, compact: bool) -> None:
    """Trigger a session-start briefing — Phase 32."""
    from deadline_agent.awareness.session_intelligence import (
        build_session_briefing,
        format_session_briefing,
    )

    async def _build():
        with SessionLocal() as session:
            return await build_session_briefing(session, life_track=life_track)

    briefing = asyncio.run(_build())
    if not briefing.priorities and not briefing.extras:
        click.echo("Nothing pressing right now — clear runway.")
        return
    click.echo(format_session_briefing(briefing, compact=compact))


@cli.command("patterns")
def patterns_command() -> None:
    """Show learned behavioral patterns."""
    from deadline_agent.reasoning.state import _format_pattern
    from deadline_agent.store.pattern_repository import PatternRepository

    from deadline_agent.config import settings

    with SessionLocal() as session:
        repo = PatternRepository(session)
        patterns = [
            p for p in repo.get_all()
            if p.confidence >= settings.min_pattern_confidence
        ]

    if not patterns:
        click.echo("No behavioral patterns learned yet.")
        click.echo("Patterns are detected after enough file activity and completed tasks.")
        return

    # Group by pattern_type
    by_type: dict[str, list[object]] = {}
    for p in patterns:
        by_type.setdefault(p.pattern_type, []).append(p)

    for ptype, items in by_type.items():
        click.echo(f"\n{ptype.upper().replace('_', ' ')}:")
        for p in items:
            desc = _format_pattern(p)
            conf_pct = int(p.confidence * 100)
            conf_label = "high" if conf_pct >= 80 else "medium" if conf_pct >= 50 else "low"
            click.echo(
                f"  {desc}  ({p.sample_count} observations, {conf_label} confidence)"
            )


@cli.command("affects")
def affects_command() -> None:
    """Show inferred task-type affect map."""
    from deadline_agent.awareness.task_affect import get_intervention, get_task_affect_map
    from deadline_agent.config import settings

    with SessionLocal() as session:
        affect_map = get_task_affect_map(session)

    if not affect_map:
        click.echo("No task affect patterns learned yet.")
        click.echo("Affects are inferred after enough completed tasks with work sessions.")
        return

    # Group by affect label
    by_affect: dict[str, list[tuple[str, object]]] = {}
    for task_type, affect in affect_map.items():
        by_affect.setdefault(affect.affect_label, []).append((task_type, affect))

    AFFECT_ICONS = {
        "enjoyment": "+",
        "neutral": "·",
        "avoidance": "!",
        "anxiety": "x",
    }

    for label in ("enjoyment", "neutral", "avoidance", "anxiety"):
        items = by_affect.get(label, [])
        if not items:
            continue
        icon = AFFECT_ICONS[label]
        click.echo(f"\n[{icon}] {label.upper()}:")
        for task_type, affect in items:
            conf_pct = int(affect.confidence * 100)
            evidence = affect.evidence
            parts = []
            lag = evidence.get("mean_start_lag_pct")
            if lag is not None:
                parts.append(f"start at {lag:.0%} of available time")
            no_work = evidence.get("no_work_rate")
            if no_work is not None and no_work > 0:
                parts.append(f"{no_work:.0%} skipped")
            detail = ", ".join(parts)
            click.echo(f"  {task_type}: {detail} ({conf_pct}% confidence)")
            intervention = get_intervention(label)
            if intervention:
                click.echo(f"    -> {intervention}")


@cli.command("schedule")
def schedule_command() -> None:
    """Propose a smart schedule based on calendar gaps and task priorities."""
    from deadline_agent.reasoning.scheduler import propose_smart_schedule
    from deadline_agent.reasoning.state import build_unified_context

    async def _run() -> list[str]:
        with SessionLocal() as session:
            ctx = await build_unified_context(session)
            proposals = propose_smart_schedule(ctx, session)
            return [f"[{p.id}] {p.title}" for p in proposals]

    results = asyncio.run(_run())
    if not results:
        click.echo("No schedule proposals — no unworked tasks or no calendar gaps.")
        return
    click.echo(f"Proposed {len(results)} time block(s):")
    for r in results:
        click.echo(f"  {r}")
    click.echo("Run 'deadline-agent actions list' to review.")


@cli.group("actions")
def actions_group() -> None:
    """Manage proposed actions (calendar blocks, drafts)."""


@actions_group.command("list")
def actions_list() -> None:
    """Show pending proposed actions."""
    from deadline_agent.store.action_repository import ActionRepository

    with SessionLocal() as session:
        repo = ActionRepository(session)
        actions = repo.list_pending()

    if not actions:
        click.echo("No pending actions.")
        return

    for a in actions:
        click.echo(f"[{a.id}] ({a.type}) {a.title} [{a.status}]")


@actions_group.command("approve")
@click.argument("action_id", type=int)
def actions_approve(action_id: int) -> None:
    """Approve and execute a proposed action."""
    from deadline_agent.actions.executor import execute_action
    from deadline_agent.store.action_repository import ActionRepository

    async def _run() -> bool:
        with SessionLocal() as session:
            repo = ActionRepository(session)
            action = repo.approve(action_id)
            if action is None:
                return False
            return await execute_action(session, action)

    result = asyncio.run(_run())
    if result:
        click.echo(f"Action {action_id} approved and executed.")
    else:
        click.echo(f"Action {action_id} not found or execution failed.", err=True)


@actions_group.command("reject")
@click.argument("action_id", type=int)
def actions_reject(action_id: int) -> None:
    """Reject a proposed action."""
    from deadline_agent.store.action_repository import ActionRepository

    with SessionLocal() as session:
        repo = ActionRepository(session)
        result = repo.reject(action_id)

    if result is None:
        click.echo(f"Action {action_id} not found or not pending.")
    else:
        click.echo(f"Action {action_id} rejected.")


@actions_group.command("propose-block")
@click.argument("task_id", type=int)
@click.argument("start_iso")
@click.argument("end_iso")
def actions_propose_block(task_id: int, start_iso: str, end_iso: str) -> None:
    """Propose a calendar time block for a task."""
    from deadline_agent.actions.proposer import propose_time_block

    with SessionLocal() as session:
        repo = TaskRepository(session)
        task = repo.get_task(task_id)
        if task is None:
            click.echo(f"Task {task_id} not found.", err=True)
            return
        action = propose_time_block(session, task, start_iso, end_iso)

    click.echo(f"Proposed action [{action.id}]: {action.title}")


@cli.group()
def auth() -> None:
    """Manage authentication credentials."""


@auth.command("google")
def auth_google() -> None:
    """Set up Google OAuth2 credentials (Gmail + Calendar) via loopback flow."""
    import socket
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from deadline_agent.auth import TokenManager
    from deadline_agent.config import settings

    tm = TokenManager()
    creds = tm._load_credentials()
    if creds is None:
        click.echo(f"Place your OAuth credentials at: {settings.google_credentials_path}")
        click.echo("Download from: https://console.cloud.google.com/apis/credentials")
        return

    # Bind an ephemeral loopback port — Google's replacement for the deprecated OOB flow.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}"

    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            code = (params.get("code") or [""])[0]
            error = (params.get("error") or [""])[0]
            if code:
                received["code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Auth complete.</h2> You can close this tab.")
                return
            if error:
                received["error"] = error
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h2>Auth failed:</h2> {error}".encode())
                return
            # Ignore unrelated requests (favicon, preflight, etc.)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args: object) -> None:  # silence stdout
            return

    scopes = [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
    ]
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
        }
    )

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    click.echo(f"Listening on {redirect_uri} for OAuth callback...")
    click.echo("Opening browser. If it doesn't open, visit this URL manually:")
    click.echo(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001
        pass

    try:
        while "code" not in received and "error" not in received:
            thread.join(timeout=0.5)
    finally:
        server.shutdown()

    if "error" in received:
        click.echo(f"OAuth failed: {received['error']}", err=True)
        return

    success = asyncio.run(tm.exchange_code(received["code"], redirect_uri=redirect_uri))
    if success:
        click.echo("Google authentication successful. Token saved.")
    else:
        click.echo("Failed to exchange authorization code.", err=True)


@cli.command("debrief")
@click.option(
    "--term",
    default=None,
    help='Term name, e.g. "Winter 2026". Auto-generated if omitted.',
)
@click.option(
    "--start",
    default=None,
    help="Semester start date (YYYY-MM-DD). Defaults to 4 months ago.",
)
@click.option(
    "--end",
    default=None,
    help="Semester end date (YYYY-MM-DD). Defaults to today.",
)
def debrief_command(
    term: str | None, start: str | None, end: str | None
) -> None:
    """Generate end-of-semester debrief with analytics."""
    from deadline_agent.reasoning.semester_debrief import (
        generate_semester_debrief,
        stats_to_prompt,
    )

    now = datetime.now(ZoneInfo("America/New_York"))
    if end is None:
        end = now.strftime("%Y-%m-%d")
    if start is None:
        start_dt = now.replace(month=max(1, now.month - 4))
        start = start_dt.strftime("%Y-%m-%d")
    if term is None:
        # Infer from end date
        month = now.month
        year = now.year
        if month <= 5:
            term = f"Winter {year}"
        elif month <= 8:
            term = f"Summer {year}"
        else:
            term = f"Fall {year}"

    click.echo(f"Generating debrief for {term} ({start} to {end})...")
    click.echo()

    async def _run() -> dict:  # type: ignore[type-arg]
        with SessionLocal() as session:
            return await generate_semester_debrief(
                session, start, end, term  # type: ignore[arg-type]
            )

    result = asyncio.run(_run())

    # Print stats summary
    click.echo("=" * 60)
    click.echo(f"  SEMESTER DEBRIEF — {term}")
    click.echo("=" * 60)
    click.echo()
    click.echo(stats_to_prompt(result["stats"]))
    click.echo()
    click.echo("-" * 60)
    click.echo("NARRATIVE RETROSPECTIVE")
    click.echo("-" * 60)
    click.echo()
    click.echo(result["debrief_text"])
    click.echo()
    click.echo(f"(Saved as record #{result['record_id']})")


@cli.group("lora")
def lora_group() -> None:
    """LoRA fine-tuning data management."""


@lora_group.command("stats")
def lora_stats() -> None:
    """Show training data statistics."""
    from deadline_agent.lora.export import get_training_stats

    with SessionLocal() as session:
        stats = get_training_stats(session)

    ext = stats["extraction_logs"]
    click.echo("EXTRACTION LOGS:")
    click.echo(f"  Total: {ext['total']}  (accepted: {ext['accepted']}, rejected: {ext['rejected']})")
    if ext["avg_confidence"] is not None:
        click.echo(f"  Avg confidence (accepted): {ext['avg_confidence']}")

    dig = stats["digest_logs"]
    click.echo(f"\nDIGEST LOGS: {dig['total']}")
    if dig["by_type"]:
        for ptype, count in dig["by_type"].items():
            click.echo(f"  {ptype}: {count}")

    click.echo(f"\nSYNTHETIC-ELIGIBLE TASKS: {stats['synthetic_eligible_tasks']}")

    if ext["total"] == 0 and dig["total"] == 0:
        click.echo("\nNo training data logged yet.")
        click.echo("Enable with: lora_log_training_data = true in config.toml [lora] section")


@lora_group.command("export-extraction")
@click.option("--min-confidence", default=0.75, show_default=True, help="Minimum confidence threshold")
@click.option("--output", "-o", default="extraction_pairs.jsonl", show_default=True, help="Output JSONL path")
@click.option("--synthetic", is_flag=True, help="Generate synthetic pairs from Task table instead")
def lora_export_extraction(min_confidence: float, output: str, synthetic: bool) -> None:
    """Export extraction training pairs as JSONL."""
    from deadline_agent.lora.export import (
        export_extraction_pairs,
        export_synthetic_pairs,
        write_jsonl,
    )

    with SessionLocal() as session:
        if synthetic:
            pairs = export_synthetic_pairs(session)
            click.echo(f"Generated {len(pairs)} synthetic pairs from Task table.")
        else:
            pairs = export_extraction_pairs(session, min_confidence=min_confidence)
            click.echo(f"Found {len(pairs)} extraction pairs (confidence >= {min_confidence}).")

    if not pairs:
        click.echo("No training data to export.")
        return

    count = write_jsonl(pairs, output)
    click.echo(f"Wrote {count} pairs to {output}")


@lora_group.command("export-digest")
@click.option("--output", "-o", default="digest_pairs.jsonl", show_default=True, help="Output JSONL path")
@click.option("--type", "prompt_type", default=None, type=click.Choice(["digest", "insight", "query"]),
              help="Filter by prompt type")
def lora_export_digest(output: str, prompt_type: str | None) -> None:
    """Export digest/reasoning training pairs as JSONL."""
    from deadline_agent.lora.export import export_digest_pairs, write_jsonl

    with SessionLocal() as session:
        pairs = export_digest_pairs(session, prompt_type=prompt_type)

    if not pairs:
        click.echo("No digest training data to export.")
        return

    count = write_jsonl(pairs, output)
    click.echo(f"Wrote {count} pairs to {output}")


@lora_group.command("evaluate")
@click.argument("model_tag")
@click.option("--test-file", required=True, help="Path to JSONL test pairs")
@click.option("--compare-to", default=None, help="Base model to compare against")
def lora_evaluate(model_tag: str, test_file: str, compare_to: str | None) -> None:
    """Evaluate a model against held-out test pairs."""
    from deadline_agent.lora.evaluate import compare_models, evaluate_model, load_test_pairs

    pairs = load_test_pairs(test_file)
    click.echo(f"Loaded {len(pairs)} test pairs from {test_file}")

    if compare_to:
        result = asyncio.run(compare_models(compare_to, model_tag, pairs))
        for label, data in [("BASE", result["base"]), ("FINETUNED", result["finetuned"])]:
            click.echo(f"\n{label} ({data['model']}):")
            click.echo(f"  Valid JSON: {data['valid_json']}/{data['total']}")
            click.echo(f"  Schema valid: {data['schema_valid']}/{data['total']}")
            if data["avg_confidence"]:
                click.echo(f"  Avg confidence: {data['avg_confidence']}")
            click.echo(f"  Field accuracy: {data['field_accuracy']}")

        imp = result["improvements"]
        click.echo(f"\nIMPROVEMENTS:")
        click.echo(f"  Valid JSON: {imp['valid_json_delta']:+d}")
        click.echo(f"  Schema valid: {imp['schema_valid_delta']:+d}")
        for field, delta in imp["field_accuracy_delta"].items():
            if delta != 0:
                click.echo(f"  {field}: {delta:+.3f}")
    else:
        result = asyncio.run(evaluate_model(model_tag, pairs))
        click.echo(f"\nRESULTS ({result['model']}):")
        click.echo(f"  Valid JSON: {result['valid_json']}/{result['total']}")
        click.echo(f"  Schema valid: {result['schema_valid']}/{result['total']}")
        if result["avg_confidence"]:
            click.echo(f"  Avg confidence: {result['avg_confidence']}")
        click.echo(f"  Field accuracy: {result['field_accuracy']}")


@lora_group.command("modelfile")
@click.argument("gguf_path")
@click.option("--task", type=click.Choice(["extraction", "reasoning"]), default="extraction",
              show_default=True, help="Task type determines system prompt")
@click.option("--output", "-o", default="Modelfile", show_default=True, help="Output path")
def lora_modelfile(gguf_path: str, task: str, output: str) -> None:
    """Generate an Ollama Modelfile for a LoRA-merged GGUF."""
    from pathlib import Path

    from deadline_agent.lora.modelfile import generate_modelfile

    content = generate_modelfile(gguf_path, task=task)
    Path(output).write_text(content)
    click.echo(f"Modelfile written to {output}")
    click.echo(f"Load into Ollama with: ollama create deadline-{task} -f {output}")


@cli.group("identity")
def identity_group() -> None:
    """View and manage the durable identity model."""


@identity_group.command("show")
def identity_show() -> None:
    """Display the current identity document."""
    from deadline_agent.store.identity_repository import IdentityRepository

    with SessionLocal() as session:
        repo = IdentityRepository(session)
        doc = repo.get_current()

    if doc is None:
        click.echo("No identity document yet.")
        click.echo("Run `deadline-agent identity synthesize` to generate one.")
        return

    click.echo(f"Identity Document (version {doc.version})")
    click.echo(f"Last synthesized: {doc.last_synthesis_at}")
    click.echo("=" * 60)
    click.echo(doc.document_markdown)


@identity_group.command("synthesize")
def identity_synthesize() -> None:
    """Synthesize the identity document now."""
    from deadline_agent.memory.identity_synthesizer import synthesize_identity

    async def _run() -> int:
        with SessionLocal() as session:
            doc = await synthesize_identity(session)
            return doc.version

    click.echo("Synthesizing identity...")
    version = asyncio.run(_run())
    click.echo(f"Identity document synthesized (version {version}).")
    click.echo("Run `deadline-agent identity show` to view it.")


@identity_group.command("export")
@click.option("--output", "-o", default=None, help="Output path (default: ~/identity.md)")
def identity_export(output: str | None) -> None:
    """Export the identity document as a portable markdown file."""
    from pathlib import Path

    from deadline_agent.store.identity_repository import IdentityRepository

    with SessionLocal() as session:
        repo = IdentityRepository(session)
        doc = repo.get_current()

    if doc is None:
        click.echo("No identity document to export. Run `deadline-agent identity synthesize` first.")
        return

    export_path = Path(output) if output else Path.home() / "identity.md"
    header = (
        f"# About You — Deadline Agent Identity Document\n"
        f"*Version {doc.version} | Last synthesized: {doc.last_synthesis_at}*\n"
        f"*This document is owned by you. Export it, modify it, or import it into any system.*\n\n"
    )
    export_path.write_text(header + doc.document_markdown)
    click.echo(f"Identity exported to {export_path}")


@cli.group("goals")
def goals_group() -> None:
    """Manage stated goals and track intention vs behavior gaps."""


@goals_group.command("add")
@click.argument("description")
@click.option(
    "--category",
    type=click.Choice(["academic", "recruiting", "health", "social", "personal"]),
    default="personal",
    show_default=True,
)
@click.option("--target", default=None, help='Target metric, e.g. "4h/week on CS 301"')
def goals_add(description: str, category: str, target: str | None) -> None:
    """Add a new goal."""
    from deadline_agent.store.goal_repository import GoalRepository

    with SessionLocal() as session:
        repo = GoalRepository(session)
        goal = repo.create(description, category, target)
        goal_id = goal.id
    click.echo(f"Goal [{goal_id}] created: {description}")


@goals_group.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show all goals, not just active")
def goals_list(show_all: bool) -> None:
    """List goals with current gap analysis."""
    from deadline_agent.awareness.goal_tracker import compute_goal_gaps
    from deadline_agent.store.goal_repository import GoalRepository

    with SessionLocal() as session:
        repo = GoalRepository(session)
        goals = repo.list_all() if show_all else repo.list_active()

        if not goals:
            click.echo("No goals found. Use `deadline-agent goals add` to create one.")
            return

        gaps = compute_goal_gaps(session)

    for g in goals:
        status_tag = f" [{g.status}]" if g.status != "active" else ""
        target_tag = f" (target: {g.target_metric})" if g.target_metric else ""
        click.echo(f"[{g.id}] {g.description}{target_tag} — {g.category}{status_tag}")

    if gaps:
        click.echo("\nGap analysis:")
        for gap in gaps:
            click.echo(f"  {gap}")


@goals_group.command("achieve")
@click.argument("goal_id", type=int)
def goals_achieve(goal_id: int) -> None:
    """Mark a goal as achieved."""
    from deadline_agent.store.goal_repository import GoalRepository

    with SessionLocal() as session:
        repo = GoalRepository(session)
        result = repo.update_status(goal_id, "achieved")
    if result is None:
        click.echo(f"Goal {goal_id} not found.")
    else:
        click.echo(f"Goal {goal_id} marked as achieved.")


@goals_group.command("abandon")
@click.argument("goal_id", type=int)
def goals_abandon(goal_id: int) -> None:
    """Mark a goal as abandoned."""
    from deadline_agent.store.goal_repository import GoalRepository

    with SessionLocal() as session:
        repo = GoalRepository(session)
        result = repo.update_status(goal_id, "abandoned")
    if result is None:
        click.echo(f"Goal {goal_id} not found.")
    else:
        click.echo(f"Goal {goal_id} abandoned.")


@cli.group("decide")
def decide_group() -> None:
    """Log decisions and track their outcomes over time."""


@decide_group.command("log")
@click.argument("description")
@click.option("--alternatives", default=None, help="Comma-separated alternatives considered")
@click.option("--chosen", required=True, help="The option you chose")
def decide_log(description: str, alternatives: str | None, chosen: str) -> None:
    """Log a decision."""
    from deadline_agent.store.decision_repository import DecisionRepository

    alt_list = [a.strip() for a in alternatives.split(",")] if alternatives else []

    with SessionLocal() as session:
        repo = DecisionRepository(session)
        decision = repo.create(description, chosen, alt_list)
        decision_id = decision.id
    click.echo(f"Decision [{decision_id}] logged: {description} (chose: {chosen})")


@decide_group.command("list")
@click.option("--limit", default=10, show_default=True)
def decide_list(limit: int) -> None:
    """Show recent decisions."""
    from deadline_agent.store.decision_repository import DecisionRepository

    with SessionLocal() as session:
        repo = DecisionRepository(session)
        decisions = repo.list_recent(limit=limit)

    if not decisions:
        click.echo("No decisions logged yet.")
        return

    for d in decisions:
        outcome = f" → {d.outcome}" if d.outcome else " (no outcome yet)"
        click.echo(f"[{d.id}] {d.description} — chose: {d.chosen_option}{outcome}")


@decide_group.command("outcome")
@click.argument("decision_id", type=int)
@click.argument("outcome_text")
def decide_outcome(decision_id: int, outcome_text: str) -> None:
    """Record the outcome of a past decision."""
    from deadline_agent.store.decision_repository import DecisionRepository

    with SessionLocal() as session:
        repo = DecisionRepository(session)
        result = repo.record_outcome(decision_id, outcome_text)

    if result is None:
        click.echo(f"Decision {decision_id} not found.")
    else:
        click.echo(f"Outcome recorded for decision {decision_id}.")


@cli.command("knowledge-graph")
def knowledge_graph_command() -> None:
    """Show the personal knowledge graph."""
    from deadline_agent.store.knowledge_repository import KnowledgeRepository

    with SessionLocal() as session:
        repo = KnowledgeRepository(session)
        entities = repo.list_entities(limit=30)

    if not entities:
        click.echo("No entities in the knowledge graph yet.")
        click.echo("Entities are extracted during behavioral analysis.")
        return

    by_type: dict[str, list[object]] = {}
    for e in entities:
        by_type.setdefault(e.entity_type, []).append(e)

    for etype, items in by_type.items():
        click.echo(f"\n{etype.upper()}S:")
        for e in items:
            click.echo(f"  {e.name} ({e.mention_count} mentions)")


# ── Phase 25: Recruiting intelligence ────────────────────────────────


@cli.group("recruiting")
def recruiting_group() -> None:
    """Recruiting pipeline analytics and management."""


@recruiting_group.command("pipeline")
def recruiting_pipeline() -> None:
    """Show recruiting pipeline summary."""
    from deadline_agent.store.recruiting_repository import RecruitingRepository

    with SessionLocal() as session:
        repo = RecruitingRepository(session)
        apps = repo.list_all()

    if not apps:
        click.echo("No applications tracked yet.")
        click.echo("Run: deadline-agent recruiting refresh")
        return

    # Status counts
    from collections import Counter

    statuses = Counter(a.status for a in apps)
    click.echo(f"\nPipeline: {len(apps)} applications")
    for s in ["applied", "response", "interview", "offer", "closed"]:
        if statuses.get(s):
            click.echo(f"  {s}: {statuses[s]}")

    # Active applications
    active = [a for a in apps if a.status != "closed"]
    if active:
        click.echo(f"\nActive ({len(active)}):")
        for a in active[:15]:
            tier = f" [{a.company_tier}]" if a.company_tier else ""
            click.echo(f"  {a.company_name}{tier} — {a.status}")
        if len(active) > 15:
            click.echo(f"  ...and {len(active) - 15} more")


@recruiting_group.command("report")
def recruiting_report() -> None:
    """Generate a recruiting intelligence report."""
    from deadline_agent.behavioral.recruiting_report import (
        compute_recruiting_stats,
        generate_recruiting_report,
    )

    with SessionLocal() as session:
        stats = compute_recruiting_stats(session)

    if stats["total_count"] == 0:
        click.echo("No applications tracked yet.")
        return

    click.echo("\n═══ Recruiting Intelligence Report ═══\n")

    # Pipeline stats
    click.echo(f"Applications: {stats['total_count']} total, {stats['active_count']} active")
    click.echo(f"Response rate: {stats['response_rate']:.0%}")
    click.echo(f"Stale (14+ days): {stats['stale_count']}")
    click.echo(f"New this week: {stats['new_this_week']}")

    # By tier
    by_tier = stats.get("by_tier", {})
    if by_tier:
        click.echo("\nBy tier:")
        for tier, count in sorted(by_tier.items(), key=lambda x: -x[1]):
            click.echo(f"  {tier}: {count}")

    # By status
    by_status = stats.get("by_status", {})
    if by_status:
        click.echo("\nBy status:")
        for status in ["applied", "response", "interview", "offer", "closed"]:
            if by_status.get(status):
                click.echo(f"  {status}: {by_status[status]}")

    # Fit scores
    fit = stats.get("top_fit_scores", [])
    if fit:
        click.echo("\nTop fit-scored applications:")
        for fs in fit:
            click.echo(f"  #{fs['rank']} {fs['company']} — {fs['score']:.0%}")

    # LLM narrative
    click.echo("\n--- Analysis ---\n")
    narrative = asyncio.run(generate_recruiting_report(SessionLocal()))
    click.echo(narrative)


@recruiting_group.command("classify")
@click.argument("company")
@click.argument("tier", type=click.Choice(
    ["big_tech", "mid_cap", "startup_early", "startup_growth", "finance", "other"]
))
def recruiting_classify(company: str, tier: str) -> None:
    """Manually set a company's tier classification."""
    from deadline_agent.store.recruiting_repository import RecruitingRepository

    with SessionLocal() as session:
        repo = RecruitingRepository(session)
        app = repo.find_by_company(company.lower())
        if app is None:
            click.echo(f"No application found for '{company}'.")
            return
        app.company_tier = tier
        session.commit()
        click.echo(f"Set {app.company_name} → {tier}")


@cli.command("day")
@click.option("--date", "target_date", default=None, help="YYYY-MM-DD, defaults to today")
def day_summary(target_date: str | None) -> None:
    """Show today's inferred activity timeline."""
    import json
    from datetime import date as date_type

    from deadline_agent.store.reconstruction_repository import ReconstructionRepository

    if target_date:
        try:
            dt = date_type.fromisoformat(target_date)
        except ValueError:
            click.echo(f"Invalid date format: {target_date}. Use YYYY-MM-DD.")
            return
    else:
        dt = datetime.now(ZoneInfo("America/New_York")).date()

    date_str = dt.isoformat()

    with SessionLocal() as session:
        repo = ReconstructionRepository(session)
        rec = repo.get_by_date(date_str)

        if rec is None:
            click.echo(f"No reconstruction for {date_str}. Generating now...")
            from deadline_agent.behavioral.daily_reconstruction import reconstruct_day

            rec = asyncio.run(reconstruct_day(session, dt))
            session.commit()

        segments = json.loads(rec.timeline_json)
        est = ZoneInfo("America/New_York")

        click.echo(f"\n  Day Summary — {date_str}")
        click.echo(f"  Overall confidence: {rec.overall_confidence:.0%}")
        click.echo("  " + "-" * 50)

        type_icons = {
            "work": "W",
            "meeting": "M",
            "idle": "-",
            "email": "E",
            "unknown": "?",
        }

        for seg in segments:
            icon = type_icons.get(seg.get("activity_type", "?"), "?")
            conf = seg.get("confidence", 0)
            # Confidence indicator
            if conf >= 0.8:
                conf_str = ""
            elif conf >= 0.6:
                conf_str = " ~"
            else:
                conf_str = " ?"

            # Format times
            try:
                start = datetime.fromisoformat(seg["start_time"]).astimezone(est)
                end = datetime.fromisoformat(seg["end_time"]).astimezone(est)
                time_str = f"{start.strftime('%-I:%M %p')} - {end.strftime('%-I:%M %p')}"
            except (ValueError, KeyError):
                time_str = "??:?? - ??:??"

            desc = seg.get("description", seg.get("task_title", ""))
            confirmed = seg.get("confirmed")
            conf_mark = ""
            if confirmed is True:
                conf_mark = " [confirmed]"
            elif confirmed is False:
                conf_mark = " [denied]"

            click.echo(f"  [{icon}] {time_str}  {desc}{conf_str}{conf_mark}")

        click.echo()
        if rec.narrative:
            click.echo(f"  {rec.narrative}")
        click.echo()


@cli.command("simulate")
@click.argument("scenario")
@click.option("--follow-up", "-f", default=None, help="Session ID for follow-up simulation")
def simulate_cmd(scenario: str, follow_up: str | None) -> None:
    """Run a what-if simulation: 'What if I skip class tomorrow?'"""
    from deadline_agent.reasoning.intervention_engine import init_intervention_engine

    # Ensure intervention engine is initialized for CLI context
    try:
        from deadline_agent.reasoning.intervention_engine import get_intervention_engine

        get_intervention_engine()
    except RuntimeError:
        init_intervention_engine(SessionLocal)

    from deadline_agent.reasoning.simulation import simulate_scenario_v2

    with SessionLocal() as session:
        result = asyncio.run(simulate_scenario_v2(session, scenario, follow_up))

        click.echo(f"\n  Scenario: {result.scenario}")
        click.echo(f"  Confidence: {result.confidence}")
        if result.confidence_reason:
            click.echo(f"  Reason: {result.confidence_reason}")

        if result.risk_deltas:
            click.echo("\n  Risk Deltas:")
            click.echo("  " + "-" * 60)
            for d in result.risk_deltas:
                if abs(d.delta) < 0.01:
                    continue
                sign = "+" if d.delta > 0 else ""
                arrow = "^" if d.delta > 0 else "v"
                course = f" ({d.course})" if d.course else ""
                click.echo(
                    f"  {arrow} {d.task_title}{course}: "
                    f"{d.p_miss_before:.0%} → {d.p_miss_after:.0%} "
                    f"({sign}{d.delta:.0%}) "
                    f"[{d.risk_level_before} → {d.risk_level_after}]"
                )

        if result.drop_analysis:
            da = result.drop_analysis
            click.echo(f"\n  Drop Analysis: {da['dropped_task']}")
            click.echo(f"  Freed: {da['freed_hours']:.1f}h")
            click.echo(f"  Net risk change: {da['net_risk_change']:+.0%}")
            for b in da.get("beneficiaries", []):
                click.echo(f"    Benefits: {b['task_title']} ({b['delta']:+.0%})")

        if result.recommendation:
            click.echo(f"\n  Recommendation: {result.recommendation}")

        if result.weekly_projection:
            click.echo(f"\n  Projection:\n  {result.weekly_projection[:500]}")

        click.echo(f"\n  Session ID: {result.session_id} (use -f to follow up)")
        click.echo()


@cli.command("voice")
def voice_log_cmd() -> None:
    """Record a voice update (up to 15 seconds) and apply it."""
    from deadline_agent.voice.pipeline import (
        apply_update,
        extract_structured_update,
        record_and_transcribe,
    )

    click.echo("Recording for up to 15 seconds...")

    try:
        text = record_and_transcribe(max_seconds=15)
    except ImportError as e:
        click.echo(f"Missing dependency: {e}")
        return
    except Exception as e:
        click.echo(f"Recording failed: {e}")
        return

    if not text.strip():
        click.echo("No speech detected.")
        return

    click.echo(f"Transcribed: {text}")

    with SessionLocal() as session:
        update = asyncio.run(extract_structured_update(text, session))
        click.echo(f"Detected: {update.get('update_type', 'unknown')}")

        result = asyncio.run(apply_update(update, session))
        session.commit()
        click.echo(result)
