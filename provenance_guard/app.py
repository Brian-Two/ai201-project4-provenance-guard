"""Flask API for the Provenance Guard project."""

from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable

from flask import Flask, jsonify, request
from dotenv import load_dotenv

from .labels import transparency_label
from .signals import combine_signals, llm_signal, stylometric_signal
from .storage import get_content, init_db, insert_content, list_audit, update_status_and_add_appeal, utc_now

load_dotenv()

SUBMIT_LIMIT = 5
SUBMIT_WINDOW_SECONDS = 60


class SlidingWindowLimiter:
    """Small dependency-free limiter, keyed by creator then IP."""

    def __init__(self, limit: int = SUBMIT_LIMIT, window_seconds: int = SUBMIT_WINDOW_SECONDS):
        self.limit = limit
        self.window_seconds = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self.hits[key]
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        return True, 0


def create_app(database_path: str | None = None, limiter: SlidingWindowLimiter | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = database_path or os.getenv("DATABASE_PATH", "data/provenance_guard.db")
    app.config["LIMITER"] = limiter or SlidingWindowLimiter()
    init_db(app.config["DATABASE_PATH"])

    def rate_limited(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            body = request.get_json(silent=True) or {}
            key = f"{body.get('creator_id', 'anonymous')}:{request.remote_addr or 'unknown'}"
            allowed, retry_after = app.config["LIMITER"].allow(key)
            if not allowed:
                response = jsonify({
                    "error": "rate_limit_exceeded",
                    "message": "Submission limit reached. Try again after the retry window.",
                    "retry_after_seconds": retry_after,
                })
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response
            return view(*args, **kwargs)
        return wrapped

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.post("/submit")
    @rate_limited
    def submit() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_json", "message": "Send a JSON object."}), 400
        text = body.get("text")
        creator_id = body.get("creator_id")
        if not isinstance(text, str) or len(text.strip()) < 20:
            return jsonify({"error": "invalid_text", "message": "text must contain at least 20 characters."}), 400
        if not isinstance(creator_id, str) or not creator_id.strip():
            return jsonify({"error": "invalid_creator_id", "message": "creator_id is required."}), 400

        semantic = llm_signal(text)
        stylometry = stylometric_signal(text)
        combined = combine_signals(semantic, stylometry)
        content_id = str(uuid.uuid4())
        timestamp = utc_now()
        record = {
            "content_id": content_id,
            "creator_id": creator_id,
            "text": text,
            "status": "classified",
            "attribution": combined["attribution"],
            "confidence": combined["confidence"],
            "label": transparency_label(combined["attribution"]),
            "signals": {"semantic": semantic, "stylometry": stylometry, "combined": combined},
            "created_at": timestamp,
        }
        insert_content(app.config["DATABASE_PATH"], record)
        return jsonify({
            "content_id": content_id,
            "status": record["status"],
            "attribution": record["attribution"],
            "confidence": record["confidence"],
            "label": record["label"],
            "signals": record["signals"],
            "timestamp": timestamp,
        }), 201

    @app.post("/appeal")
    def appeal() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_json", "message": "Send a JSON object."}), 400
        content_id = body.get("content_id")
        creator_id = body.get("creator_id")
        reason = body.get("reason")
        if not all(isinstance(value, str) and value.strip() for value in (content_id, creator_id, reason)):
            return jsonify({"error": "invalid_appeal", "message": "content_id, creator_id, and reason are required."}), 400
        content = get_content(app.config["DATABASE_PATH"], content_id)
        if content is None:
            return jsonify({"error": "not_found", "message": "Content was not found."}), 404
        if content["creator_id"] != creator_id:
            return jsonify({"error": "forbidden", "message": "Only the creator may appeal this classification."}), 403
        if content["status"] == "under_review":
            return jsonify({"error": "already_under_review", "message": "This content already has an open appeal."}), 409
        updated = update_status_and_add_appeal(app.config["DATABASE_PATH"], content_id, creator_id, reason.strip())
        return jsonify({
            "content_id": content_id,
            "status": "under_review",
            "appeal": {"reason": reason.strip(), "timestamp": updated["timestamp"]},
            "message": "Appeal recorded and sent for human review.",
        }), 201

    @app.get("/log")
    def audit_log() -> Any:
        return jsonify({"entries": list_audit(app.config["DATABASE_PATH"])})

    @app.get("/content/<content_id>")
    def content_detail(content_id: str) -> Any:
        content = get_content(app.config["DATABASE_PATH"], content_id)
        if content is None:
            return jsonify({"error": "not_found"}), 404
        content.pop("text", None)
        return jsonify(content)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)

