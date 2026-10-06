# Provenance Guard plan

## Goal and architecture decisions

Provenance Guard is a small Flask service for a writing platform. A creator sends text and a creator ID to `POST /submit`. The service validates the request, runs two independent signals, combines their AI-likelihood scores, maps the result to an attribution category and plain-language transparency label, persists the content and classification in SQLite, and returns the structured result. A creator can then send `POST /appeal` with a reason; the service changes the content status to `under_review` and appends an appeal event to the same audit log.

The design is intentionally cautious about false positives. A score is evidence strength, not proof of authorship. Signal disagreement lowers confidence and routes the result to the uncertain label, where the creator has a visible appeal path.

## Detection signals and scoring

### Signal 1: semantic/style assessment

When `GROQ_API_KEY` is configured, `llm_signal` asks the Groq Llama model for an `ai_likelihood` from 0 to 1. This signal captures holistic semantic and stylistic patterns such as unusually consistent phrasing, generic transitions, and the relationship between ideas. It can notice patterns that simple counts miss, but it can be influenced by genre, prompt context, model bias, short inputs, and the same polished style that a human editor can produce. It cannot prove who wrote the text.

For reproducible local testing, a missing key uses a labeled local fallback. The fallback measures surface regularity, vocabulary repetition, and repeated adjacent words. That fallback is deliberately not described as an LLM result.

### Signal 2: stylometric structure

`stylometric_signal` measures sentence-length coefficient of variation, type-token ratio (unique words divided by total words), and punctuation density. AI-like evidence increases when sentence rhythm is unusually uniform and vocabulary diversity is narrow; punctuation density is a small adjustment. This captures measurable structure without interpreting meaning, but it can misread poems, lyrics, edited human prose, short text, lists, or writers with a consistent personal style. It returns its own `ai_likelihood` from 0 to 1 plus the underlying metrics.

### Combining outputs

Both signals use the same direction: `0` means more human-like evidence and `1` means more AI-like evidence. The combined AI-likelihood is:

```text
combined_ai = 0.60 * semantic_ai_likelihood + 0.40 * stylometric_ai_likelihood
agreement = 1 - abs(semantic_score - stylometric_score)
direction_strength = abs(combined_ai - 0.50) * 2
confidence = 0.55 * direction_strength + 0.45 * agreement
```

The semantic signal receives more weight because it can use meaning and context, while the structural signal is more brittle. The agreement term prevents two conflicting signals from appearing highly certain.

## Uncertainty and thresholds

The API returns a confidence between 0 and 1. In this project, confidence means how strongly the two available signals agree on a direction, not the probability that an authorship claim is true.

| Combined evidence and confidence | Attribution | Meaning |
| --- | --- | --- |
| `combined_ai >= 0.70` and `confidence >= 0.75` | `likely_ai` | strong AI-like evidence, still appealable |
| `combined_ai <= 0.30` and `confidence >= 0.75` | `likely_human` | strong human-like evidence, not proof |
| every other combination | `uncertain` | evidence is mixed, close to the middle, or not strong enough |

This deliberately creates a broad uncertain band. A result near 0.50 should not be forced into a binary label. I validated the design with a polished, uniform paragraph, a fragmented late-night draft, and a third narrative sample. The tests assert that the submissions return both signals and that confidence changes between visibly different inputs. The offline demo shows the exact scores produced by the current implementation.

## Transparency labels

These are the exact user-facing strings returned by the API:

| Variant | Exact text |
| --- | --- |
| High-confidence AI | “This piece shows strong signs of AI-assisted writing. The signals are not proof of authorship; the creator can appeal this result.” |
| Uncertain | “We couldn't confidently determine whether this piece was written by a person or generated with AI. Treat this result as uncertain; the creator can appeal.” |
| High-confidence human | “This piece shows strong signs of human writing. Detection is not proof of authorship, but the signals support this result.” |

## Appeals workflow

The creator who owns a content ID submits `POST /appeal` with `content_id`, `creator_id`, and a non-empty `reason`. The API verifies the content exists and that the creator ID matches. It then changes the content status from `classified` to `under_review`, records the creator's reasoning and timestamp, and appends an `event_type: appeal` entry carrying the original attribution, confidence, and signals. A reviewer opening `GET /log` can see the classification event immediately before the appeal event and compare the reason with the original evidence. Repeated appeals are rejected with `409` until a human workflow resolves the case.

## Anticipated edge cases

1. A poem or lyric with short repeated lines may have very low sentence-length variation and a low type-token ratio. The stylometric signal may call it AI-like even when the repetition is an intentional artistic device, so the broad uncertain category and appeal path matter.
2. A heavily edited human essay may use consistent sentence lengths, formal transitions, and a high vocabulary ratio. Both signals may overestimate AI assistance because editing changes the same surface properties the detector observes.
3. A very short submission, a bullet list, or code-like text has too little natural language context. Scores should stay near the uncertain region rather than being presented as proof; the current API enforces a 20-character minimum but does not claim reliable detection for these genres.

## Architecture

```text
Submission flow

POST /submit --raw text + creator_id--> validation
     validation --accepted text--> semantic signal (0..1 AI-likelihood)
     validation --accepted text--> stylometric signal (metrics + 0..1)
     semantic + stylometry --two scores--> confidence combiner
     combiner --attribution + confidence--> label mapper
     label mapper --plain-language label--> SQLite content + audit log
     SQLite --content_id, signals, label, timestamp--> JSON response

Appeal flow

POST /appeal --content_id + creator_id + reason--> ownership check
     ownership check --valid creator--> status update: under_review
     status update --original decision + reason--> SQLite audit log
     SQLite --appeal timestamp + reason--> JSON response
```

The submission flow keeps both individual signal results in the response and in the audit log, so a reviewer can see how the combined score was formed. The appeal flow does not silently reclassify content; it preserves the original decision and adds a reviewable event.

## API contract

- `POST /submit`: JSON `{ "text": string, "creator_id": string }`; returns `201` with `content_id`, `attribution`, `confidence`, `label`, `signals`, and timestamp.
- `POST /appeal`: JSON `{ "content_id": string, "creator_id": string, "reason": string }`; returns `201` with `under_review` status.
- `GET /log`: returns `{ "entries": [...] }`, where each entry is structured and includes timestamp, attribution, confidence, signals, and event type.
- `GET /content/<content_id>`: returns the current status and classification metadata without exposing the submitted text.

## Rate-limit decision

`POST /submit` permits 5 requests per creator/IP key per 60 seconds. Five submissions per minute supports a creator revising or testing several drafts, while stopping a script from flooding the audit log or consuming the optional LLM quota. A `429` response includes `Retry-After` and a JSON error. Appeals are not rate-limited in this prototype because they are already tied to an owned content ID and should remain available for a legitimate contested result.

## AI Tool Plan

### M3: submission endpoint and first signal

Provide the AI tool the Detection signals section, API contract, and the Architecture diagram. Ask it to generate the Flask app skeleton with a `POST /submit` stub and the semantic signal function with the specified 0-to-1 output. Before integrating, run the function directly against a polished paragraph, a casual draft, and a short input; compare its signature and output with this specification and revise any unsupported claims.

### M4: second signal and confidence scoring

Provide the AI tool the Detection signals, Combining outputs, Uncertainty and thresholds, and Architecture sections. Ask for the standalone stylometric function and the exact weighted confidence formula. Verify the generated code against the 0.60/0.40 weights, agreement penalty, and three threshold categories; run the same sample texts and confirm the scores and labels vary meaningfully before wiring the route.

### M5: production layer

Provide the AI tool the Transparency labels, Appeals workflow, Rate-limit decision, API contract, and Architecture sections. Ask for label mapping, the `/appeal` endpoint, and structured audit events. Verify that all three label strings are reachable with boundary tests, that an appeal changes status to `under_review`, that the creator reason is retained, and that the appeal appears beside the original classification entry.

## Stretch-feature decision

The implementation includes a third structural metric inside the stylometric signal, but it does not claim the optional three-signal ensemble because those metrics are one signal family. The required features are prioritized so the confidence and appeal behavior remain explainable.

