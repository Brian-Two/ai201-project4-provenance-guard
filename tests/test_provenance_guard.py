import json

import pytest

from provenance_guard.app import SlidingWindowLimiter, create_app
from provenance_guard.signals import combine_signals


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"), SlidingWindowLimiter(limit=5, window_seconds=60))
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def submit(client, text, creator="tester"):
    response = client.post("/submit", json={"text": text, "creator_id": creator})
    assert response.status_code == 201
    return response.get_json()


def test_submit_returns_two_signals_confidence_and_label(client):
    result = submit(client, "The quarterly report presents a coherent framework. Each section follows the same polished structure and reinforces the central conclusion with measured clarity.")
    assert result["attribution"] in {"likely_ai", "likely_human", "uncertain"}
    assert 0 <= result["confidence"] <= 1
    assert "semantic" in result["signals"]
    assert "stylometry" in result["signals"]
    assert isinstance(result["label"], str) and len(result["label"]) > 20


def test_different_inputs_produce_different_confidence_scores(client):
    polished = submit(client, "The system delivers a consistent and comprehensive experience. It organizes information clearly, supports reliable decisions, and maintains a balanced tone throughout each carefully structured section.", "a")
    irregular = submit(client, "wait—no. I wrote this at 2am, then deleted half of it. Maybe the ending works? maybe not! coffee, rain, one bright window, and a sentence I can't quite finish", "b")
    assert polished["confidence"] != irregular["confidence"]


def test_appeal_updates_status_and_appears_in_audit_log(client):
    result = submit(client, "A short story moves through a quiet station, where an ordinary goodbye becomes an unexpected beginning for its narrator.", "creator-1")
    appeal = client.post("/appeal", json={
        "content_id": result["content_id"],
        "creator_id": "creator-1",
        "reason": "I drafted this in my notebook before revising it here; the repeated phrasing is intentional.",
    })
    assert appeal.status_code == 201
    assert client.get(f"/content/{result['content_id']}").get_json()["status"] == "under_review"
    entries = client.get("/log").get_json()["entries"]
    assert len(entries) == 2
    assert entries[-1]["event_type"] == "appeal"
    assert entries[-1]["appeal_reason"].startswith("I drafted")
    assert "confidence" in entries[-1] and "timestamp" in entries[-1]


def test_rate_limit_returns_429(client):
    for index in range(5):
        result = client.post("/submit", json={"text": f"This is a valid submission number {index} with enough text for the API.", "creator_id": "flooder"})
        assert result.status_code == 201
    limited = client.post("/submit", json={"text": "This sixth request should be rejected by the submission limit.", "creator_id": "flooder"})
    assert limited.status_code == 429
    assert limited.get_json()["error"] == "rate_limit_exceeded"
    assert "Retry-After" in limited.headers


def test_all_three_attribution_categories_are_reachable():
    assert combine_signals({"ai_likelihood": 0.95}, {"ai_likelihood": 0.90})["attribution"] == "likely_ai"
    assert combine_signals({"ai_likelihood": 0.10}, {"ai_likelihood": 0.15})["attribution"] == "likely_human"
    assert combine_signals({"ai_likelihood": 0.60}, {"ai_likelihood": 0.40})["attribution"] == "uncertain"
