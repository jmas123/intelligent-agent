"""
title: Deadline Digest
description: Get a summary digest of upcoming deadlines from the Deadline Agent.
author: deadline-agent
version: 0.1.0
"""

import json

import requests

BASE_URL = "http://host.docker.internal:8000/api/chat"


class Tools:
    def __init__(self) -> None:
        self.base_url = BASE_URL

    def get_morning_digest(self) -> str:
        """Get a summary of upcoming deadlines for the next 7 days.
        Use when the user asks for an overview, summary, or digest of
        what's coming up."""
        resp = requests.get(f"{self.base_url}/digest", timeout=10)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
