"""
title: Deadline Gmail
description: Search Gmail messages from the Deadline Agent.
author: deadline-agent
version: 0.1.0
"""

import json

import requests

BASE_URL = "http://host.docker.internal:8000/api/chat"


class Tools:
    def __init__(self) -> None:
        self.base_url = BASE_URL

    def search_emails(self, query: str, max_results: int = 10) -> str:
        """Search your Gmail inbox. Use this when the user asks about emails,
        messages, replies, or communications they've received.

        Examples: "emails from recruiters", "replies about project",
        "is:unread from:professor", "subject:interview after:2026/03/01"

        The query supports Gmail search syntax (from:, subject:, is:unread, etc).
        """
        resp = requests.post(
            f"{self.base_url}/search-gmail",
            json={"query": query, "max_results": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
