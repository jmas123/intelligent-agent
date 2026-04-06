#!/usr/bin/env python3
"""Seed realistic data for testing Phase 9 behavioral memory.

Usage:
    uv run python scripts/seed_behavioral_data.py

Then test with:
    uv run deadline-agent patterns                    # should say "no patterns yet"
    uv run python -c "
from deadline_agent.store.session import SessionLocal, init_db
from deadline_agent.behavioral.session_inference import infer_sessions
from deadline_agent.behavioral.analyzer import run_all_analyses
init_db()
with SessionLocal() as s:
    sessions = infer_sessions(s)
    print(f'Inferred {len(sessions)} work sessions')
    patterns = run_all_analyses(s)
    print(f'Generated {len(patterns)} patterns')
"
    uv run deadline-agent patterns                    # should show learned patterns
    curl -s localhost:8000/api/chat/patterns | python3 -m json.tool   # if server running
"""

import hashlib
from datetime import datetime, timedelta

from deadline_agent.models import FileActivity, FileTaskLink, Task
from deadline_agent.store.session import SessionLocal, init_db


def main() -> None:
    init_db()

    with SessionLocal() as session:
        now = datetime.utcnow()

        # --- Tasks: mix of types and statuses ---
        tasks_data = [
            {
                "title": "Submit Problem Set 4",
                "due_date_iso": (now - timedelta(days=3)).isoformat(),
                "source": "gmail",
                "type": "assignment",
                "course": "CS 101",
                "urgency_score": 4,
                "confidence": 0.95,
                "status": "done",
                "created_at": now - timedelta(days=10),
            },
            {
                "title": "Midterm Exam",
                "due_date_iso": (now - timedelta(days=1)).isoformat(),
                "source": "moodle",
                "type": "exam",
                "course": "MATH 201",
                "urgency_score": 5,
                "confidence": 0.98,
                "status": "done",
                "created_at": now - timedelta(days=14),
            },
            {
                "title": "Discussion Post Week 8",
                "due_date_iso": (now - timedelta(days=5)).isoformat(),
                "source": "moodle",
                "type": "assignment",
                "course": "ENGL 102",
                "urgency_score": 2,
                "confidence": 0.88,
                "status": "done",
                "created_at": now - timedelta(days=7),
            },
            {
                "title": "Lab Report 3",
                "due_date_iso": (now + timedelta(days=4)).isoformat(),
                "source": "gmail",
                "type": "assignment",
                "course": "PHYS 150",
                "urgency_score": 3,
                "confidence": 0.91,
                "status": "pending",
                "created_at": now - timedelta(days=5),
            },
            {
                "title": "Team Meeting Prep",
                "due_date_iso": (now + timedelta(days=1)).isoformat(),
                "source": "gcal",
                "type": "meeting",
                "course": "CS 101",
                "urgency_score": 3,
                "confidence": 0.85,
                "status": "done",
                "created_at": now - timedelta(days=2),
            },
        ]

        created_tasks = []
        for td in tasks_data:
            td["raw_hash"] = hashlib.sha256(
                f"seed_{td['title']}".encode()
            ).hexdigest()
            task = Task(**td)
            session.add(task)
            session.flush()
            created_tasks.append(task)
            print(f"  Created task: {task.title} (id={task.id})")

        # --- File activity: simulate work patterns ---
        # CS 101 Problem Set: worked on it across multiple sessions
        ps4 = created_tasks[0]
        ps4_times = [
            # Session 1: 8 days ago, evening (9-10:30 PM) — 90 min
            (now - timedelta(days=8, hours=-21), 10),
            (now - timedelta(days=8, hours=-21, minutes=-15), 10),
            (now - timedelta(days=8, hours=-21, minutes=-30), 10),
            (now - timedelta(days=8, hours=-21, minutes=-50), 10),
            (now - timedelta(days=8, hours=-21, minutes=-70), 10),
            (now - timedelta(days=8, hours=-21, minutes=-90), 10),
            # Session 2: 5 days ago, afternoon (2-3 PM) — 60 min
            (now - timedelta(days=5, hours=-14), 10),
            (now - timedelta(days=5, hours=-14, minutes=-20), 10),
            (now - timedelta(days=5, hours=-14, minutes=-40), 10),
            (now - timedelta(days=5, hours=-14, minutes=-60), 10),
            # Session 3: 3 days ago, morning (10-11:30 AM) — 90 min
            (now - timedelta(days=3, hours=-10), 10),
            (now - timedelta(days=3, hours=-10, minutes=-25), 10),
            (now - timedelta(days=3, hours=-10, minutes=-50), 10),
            (now - timedelta(days=3, hours=-10, minutes=-75), 10),
            (now - timedelta(days=3, hours=-10, minutes=-90), 10),
        ]
        for ts, _ in ps4_times:
            _add_activity(session, ps4, ts, "cs101_ps4.py")

        # MATH 201 Midterm: crammed the night before
        midterm = created_tasks[1]
        midterm_times = [
            # One big session: night before, 8 PM - 1 AM = 300 min
            (now - timedelta(days=2, hours=-20), 10),
            (now - timedelta(days=2, hours=-20, minutes=-30), 10),
            (now - timedelta(days=2, hours=-20, minutes=-60), 10),
            (now - timedelta(days=2, hours=-20, minutes=-90), 10),
            (now - timedelta(days=2, hours=-20, minutes=-120), 10),
            (now - timedelta(days=2, hours=-20, minutes=-150), 10),
            (now - timedelta(days=2, hours=-20, minutes=-180), 10),
            (now - timedelta(days=2, hours=-20, minutes=-210), 10),
            (now - timedelta(days=2, hours=-20, minutes=-240), 10),
            (now - timedelta(days=2, hours=-20, minutes=-270), 10),
            (now - timedelta(days=2, hours=-20, minutes=-300), 10),
        ]
        for ts, _ in midterm_times:
            _add_activity(session, midterm, ts, "math201_review.pdf")

        # ENGL 102 Discussion: quick 30-min session the day before
        disc = created_tasks[2]
        disc_times = [
            (now - timedelta(days=6, hours=-22), 10),
            (now - timedelta(days=6, hours=-22, minutes=-15), 10),
            (now - timedelta(days=6, hours=-22, minutes=-30), 10),
        ]
        for ts, _ in disc_times:
            _add_activity(session, disc, ts, "week8_discussion.docx")

        # PHYS 150 Lab Report: started yesterday
        lab = created_tasks[3]
        lab_times = [
            (now - timedelta(days=1, hours=-14), 10),
            (now - timedelta(days=1, hours=-14, minutes=-20), 10),
            (now - timedelta(days=1, hours=-14, minutes=-45), 10),
        ]
        for ts, _ in lab_times:
            _add_activity(session, lab, ts, "lab3_report.tex")

        # Team meeting prep: quick 20 min
        meeting = created_tasks[4]
        meet_times = [
            (now - timedelta(days=1, hours=-10), 10),
            (now - timedelta(days=1, hours=-10, minutes=-10), 10),
            (now - timedelta(days=1, hours=-10, minutes=-20), 10),
        ]
        for ts, _ in meet_times:
            _add_activity(session, meeting, ts, "meeting_notes.md")

        session.commit()
        print(f"\nSeeded {len(created_tasks)} tasks with file activity.")
        print("Now run session inference + pattern analysis (see docstring).")


def _add_activity(session, task, modified_at, filename):
    fa = FileActivity(
        path=f"/Users/student/Documents/{task.course}/{filename}",
        filename=filename,
        directory=f"/Users/student/Documents/{task.course}",
        size_bytes=2048,
        modified_at=modified_at,
        event_type="modified",
    )
    session.add(fa)
    session.flush()
    link = FileTaskLink(
        file_activity_id=fa.id,
        task_id=task.id,
        confidence=0.85,
        method="string",
    )
    session.add(link)
    session.flush()


if __name__ == "__main__":
    main()
