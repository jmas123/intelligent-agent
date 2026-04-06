"""
title: Deadline Tasks
description: Query and manage deadline tasks from the Deadline Agent.
author: deadline-agent
version: 0.1.0
"""

import json
from typing import Any

import requests

BASE_URL = "http://host.docker.internal:8000/api/chat"


class Tools:
    def __init__(self) -> None:
        self.base_url = BASE_URL

    def get_context_snapshot(self) -> str:
        """Get a full snapshot of the user's current state: tasks due today,
        this week, overdue items, and proactive alerts. Call this at the start
        of every conversation or when the user asks broad questions like
        'what should I work on?' or 'what's going on?'."""
        resp = requests.get(f"{self.base_url}/context", timeout=10)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def get_upcoming_tasks(
        self, status: str = "pending", limit: int = 10
    ) -> str:
        """Get a list of upcoming tasks. Use when the user asks about their
        deadlines, assignments, or what's due. Filter by status (pending, done,
        dismissed)."""
        resp = requests.get(
            f"{self.base_url}/tasks",
            params={"status": status, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def get_tasks_due_today(self) -> str:
        """Get tasks that are due today. Use when the user asks specifically
        about today's deadlines."""
        resp = requests.get(
            f"{self.base_url}/tasks",
            params={"due_today": True, "status": "pending"},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def get_tasks_due_this_week(self) -> str:
        """Get all tasks due in the next 7 days. Use when the user asks
        what's due this week or wants a weekly overview."""
        resp = requests.get(
            f"{self.base_url}/tasks",
            params={"due_this_week": True, "status": "pending"},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def get_task_detail(self, task_id: int) -> str:
        """Get details about a specific task by its ID."""
        resp = requests.get(f"{self.base_url}/tasks/{task_id}", timeout=10)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def mark_task_done(self, task_id: int) -> str:
        """Mark a task as done. Use when the user says they finished or
        completed something."""
        resp = requests.patch(
            f"{self.base_url}/tasks/{task_id}/status",
            json={"status": "done"},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)

    def dismiss_task(self, task_id: int) -> str:
        """Dismiss a task so it no longer appears in active lists. Use when
        the user says to ignore, remove, or dismiss a task."""
        resp = requests.patch(
            f"{self.base_url}/tasks/{task_id}/status",
            json={"status": "dismissed"},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
