"""Offline rubric demo: three classifications, one appeal, and a 429."""

import json
import tempfile

from provenance_guard.app import SlidingWindowLimiter, create_app


def show(title, response):
    print(f"\n=== {title} [{response.status_code}] ===")
    print(json.dumps(response.get_json(), indent=2))


with tempfile.NamedTemporaryFile(suffix=".db") as database:
    app = create_app(database.name, SlidingWindowLimiter(limit=3, window_seconds=60))
    with app.test_client() as client:
        polished = client.post("/submit", json={
        "creator_id": "demo-ai",
        "text": "The system delivers reliable results. The system delivers reliable results. The system delivers reliable results. The system delivers reliable results.",
        })
        show("high-confidence style example", polished)

        uncertain = client.post("/submit", json={
        "creator_id": "demo-uncertain",
        "text": "I kept the first line, lost the second—then found it under a cup. Is this a poem? Maybe. The room hummed, stopped, hummed again, and I laughed.",
        })
        show("lower-confidence style example", uncertain)

        human = client.post("/submit", json={
        "creator_id": "demo-human",
        "text": "Ah. Then the day unfolded in a way no plan could have predicted, with three interruptions, a lost key, and a neighbor asking whether the blue bicycle was mine.",
        })
        show("third classification", human)

        content_id = uncertain.get_json()["content_id"]
        appeal = client.post("/appeal", json={
        "content_id": content_id,
        "creator_id": "demo-uncertain",
        "reason": "I wrote this in my notebook and the fragmented punctuation is intentional.",
        })
        show("appeal", appeal)
        show("structured audit log", client.get("/log"))

        limited = None
        for _ in range(4):
            limited = client.post("/submit", json={
            "creator_id": "demo-rate-limit",
            "text": "A repeated demo request with enough words to pass validation.",
            })
        show("rate limit after three requests", limited)
