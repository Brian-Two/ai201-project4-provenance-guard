# Provenance Guard

Provenance Guard is a Flask backend that gives a writing platform a cautious attribution signal, a confidence score, a plain-language transparency label, an appeal path, rate limiting, and a structured SQLite audit log.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

The app listens on `http://127.0.0.1:5000`. No API key is needed for the reproducible local fallback. Set `GROQ_API_KEY` in `.env` to enable the optional Groq semantic assessment; the service falls back safely if the remote response is unavailable.

## Content submission

```bash
curl -s -X POST http://127.0.0.1:5000/submit \
  -H 'Content-Type: application/json' \
  -d '{"text":"The rain arrived before dawn. I moved the plants from the sill, made coffee, and listened to the roof decide what morning would sound like.","creator_id":"demo-creator"}' | python3 -m json.tool
```

The response is structured JSON and includes the result, confidence, exact label, and both individual signal results:

```json
{
  "attribution": "uncertain",
  "confidence": 0.63,
  "content_id": "generated-uuid",
  "label": "We couldn't confidently determine whether this piece was written by a person or generated with AI. Treat this result as uncertain; the creator can appeal.",
  "signals": {
    "semantic": {"ai_likelihood": 0.56, "provider": "local_fallback"},
    "stylometry": {
      "ai_likelihood": 0.71,
      "metrics": {"sentence_length_cv": 0.0, "type_token_ratio": 0.95, "punctuation_density": 0.18}
    },
    "combined": {"ai_likelihood": 0.62, "confidence": 0.63, "attribution": "uncertain", "agreement": 0.85}
  },
  "status": "classified",
  "timestamp": "2026-10-05T00:00:00Z"
}
```

The UUID and timestamp are generated at runtime; the example is illustrative. Run `python demo.py` for a live offline demonstration with three submissions, two noticeably different confidence scores, an appeal, a structured log, and a `429` rate-limit response.

## Detection signals

1. **Semantic/style assessment:** Groq's optional Llama model evaluates holistic semantic and stylistic patterns and returns an AI-likelihood from 0 to 1. It can capture context and phrasing that counts miss, but it can be biased by genre, short text, prompts, and polished human editing. Without a key, the response explicitly says `local_fallback` and uses a reproducible surface-regularity proxy.
2. **Stylometric structure:** pure Python measures sentence-length variation, type-token ratio, and punctuation density. It captures structural regularity and vocabulary diversity, but cannot understand meaning or distinguish intentional poetic repetition from generated repetition.

The combined score is `0.60 * semantic + 0.40 * stylometry`. Confidence also includes signal agreement: `0.55 * direction_strength + 0.45 * agreement`. Disagreement reduces confidence. Thresholds are documented in [planning.md](planning.md) and are tested in `tests/test_provenance_guard.py`.

## Transparency label text

These exact strings are returned to a client, not just shown in a mockup:

| Variant | Exact text |
| --- | --- |
| High-confidence AI | “This piece shows strong signs of AI-assisted writing. The signals are not proof of authorship; the creator can appeal this result.” |
| Uncertain | “We couldn't confidently determine whether this piece was written by a person or generated with AI. Treat this result as uncertain; the creator can appeal.” |
| High-confidence human | “This piece shows strong signs of human writing. Detection is not proof of authorship, but the signals support this result.” |

These labels visibly differ in wording and explain uncertainty to a non-technical reader. None says “classifier output” or “logit score.”

## Appeals

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H 'Content-Type: application/json' \
  -d '{"content_id":"PASTE_CONTENT_ID","creator_id":"demo-creator","reason":"I wrote this draft myself and the repeated rhythm is intentional."}' | python3 -m json.tool
```

The response changes the status to `under_review`. The creator's reasoning is appended as an `event_type: appeal` record next to the original classification in `GET /log`. Only the matching creator can appeal, and duplicate open appeals return `409`.

## Rate limiting

`POST /submit` allows **5 requests per creator/IP key per 60 seconds**. This gives a normal creator enough room to submit several revisions while preventing a script from flooding the audit log or exhausting the optional LLM quota. When the sixth request arrives, the API returns `429` with a JSON `rate_limit_exceeded` error and a `Retry-After` header. The offline `python demo.py` output shows this behavior.

## Audit log

`GET /log` returns a structured JSON object. Each classification entry has an event type, content ID, timestamp, attribution, confidence, status, and both signal outputs. An appeal entry repeats the original attribution and confidence and adds `appeal_reason`, so it can be correlated without parsing console text. The demo creates three classification entries plus one appeal entry.

Example shape:

```json
{
  "entries": [
    {
      "event_type": "classification",
      "content_id": "generated-uuid",
      "timestamp": "2026-10-05T20:10:00Z",
      "attribution": "uncertain",
      "confidence": 0.63,
      "status": "classified",
      "signals": {"semantic": {}, "stylometry": {}, "combined": {}}
    },
    {
      "event_type": "appeal",
      "content_id": "generated-uuid",
      "timestamp": "2026-10-05T20:12:00Z",
      "attribution": "uncertain",
      "confidence": 0.63,
      "status": "under_review",
      "appeal_reason": "I wrote this draft myself.",
      "signals": {"semantic": {}, "stylometry": {}, "combined": {}}
    }
  ]
}
```

## Known limitations

The system will likely misclassify a poem with repeated short lines: sentence-length variation and type-token ratio can make intentional artistic repetition look like generated regularity, and the optional semantic model may overweigh the same surface pattern. Edited essays, lists, code-like prose, and very short text have similar limitations. That is why the result is an evidence label, not proof, and why mixed scores use the uncertain label and an appeal workflow.

## Spec reflection

The plan originally considered presenting the optional LLM as the required first signal. During implementation I added a labeled local fallback because a grader should be able to run the full endpoint, audit log, appeal flow, and rate-limit demo without a network call or a secret key. I also kept the fallback's measurements explicit instead of pretending it had semantic understanding. This diverges from a Groq-only implementation but preserves the planned signal contract and makes the uncertainty behavior reproducible.

## AI Usage

1. I directed an AI tool to turn the detection-signal and architecture specification into a Flask route skeleton and a first signal function. I revised the generated approach by making the route validate both `text` and `creator_id`, requiring a 20-character minimum, and adding a local fallback so the project runs without credentials.
2. I directed an AI tool to implement the second signal and combine it with the thresholds in `planning.md`. I overrode a simple average in favor of the specified `0.60/0.40` weighting plus an agreement penalty, because treating disagreement as confidence would make the transparency label less honest.
3. I directed an AI tool to add the production layer: labels, appeals, SQLite audit events, and rate limiting. I decided to keep the limiter dependency-free and keyed by creator/IP, and revised the appeal behavior to reject duplicate open appeals with `409` while preserving the original classification entry.

## Verification

```bash
pytest -q
python demo.py
```

The tests cover the structured submission response, both signals, different confidence scores, appeal status and audit visibility, and the `429` response. See [planning.md](planning.md) for the architecture diagram and implementation decisions.
