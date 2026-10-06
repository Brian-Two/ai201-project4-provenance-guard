"""The two independent detection signals used by Provenance Guard.

Both signals return an AI-likelihood in [0, 1]. A high value means the
signal sees more evidence of AI-assisted writing; it is not a probability of
authorship and is never presented as proof.
"""

from __future__ import annotations

import math
import os
import re
import statistics
from typing import Any


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?(?=\s|$)")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(max(low, min(high, value)), 4)


def _words(text: str) -> list[str]:
    return [match.group(0).lower() for match in WORD_RE.finditer(text)]


def _sentence_lengths(text: str) -> list[int]:
    sentences = [sentence.strip() for sentence in SENTENCE_RE.findall(text) if sentence.strip()]
    return [len(_words(sentence)) for sentence in sentences] or [len(_words(text))]


def _local_semantic_signal(text: str) -> dict[str, Any]:
    """Reproducible offline proxy for the optional LLM signal.

    The proxy measures surface regularity and repeated phrase structure. It is
    deliberately described as a fallback, not as an LLM or as proof of origin.
    """

    words = _words(text)
    if not words:
        return {"ai_likelihood": 0.5, "provider": "local_fallback", "reason": "empty_text"}

    sentences = _sentence_lengths(text)
    unique_ratio = len(set(words)) / len(words)
    mean_length = statistics.mean(sentences)
    length_cv = (statistics.pstdev(sentences) / mean_length) if mean_length else 0.0
    repeated_openers = sum(
        1 for left, right in zip(words, words[1:]) if left == right
    ) / max(1, len(words) - 1)

    # Uniform sentence rhythm and unusually polished vocabulary are weak
    # signals, so this proxy remains close to 0.5 for ordinary short text.
    regularity = 1.0 - min(1.0, length_cv / 0.9)
    narrow_vocabulary = 1.0 - min(1.0, unique_ratio / 0.85)
    ai_likelihood = _clamp(0.15 + 0.45 * regularity + 0.35 * narrow_vocabulary + 0.05 * min(1.0, repeated_openers * 30))

    return {
        "ai_likelihood": ai_likelihood,
        "provider": "local_fallback",
        "features": {
            "sentence_length_cv": round(length_cv, 4),
            "unique_word_ratio": round(unique_ratio, 4),
            "adjacent_word_repetition": round(repeated_openers, 4),
        },
    }


def llm_signal(text: str) -> dict[str, Any]:
    """Ask Groq for a semantic/style assessment when configured.

    A strict JSON response is requested, but any unavailable/malformed remote
    response safely falls back to the local, reproducible proxy.
    """

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _local_semantic_signal(text)

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Assess text for signs of AI-assisted writing. Return JSON only: "
                        '{"ai_likelihood": number, "reason": string}. '
                        "Use a cautious score from 0 to 1. This is not proof of authorship."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        import json

        parsed = json.loads(response.choices[0].message.content)
        score = _clamp(float(parsed["ai_likelihood"]))
        return {
            "ai_likelihood": score,
            "provider": "groq",
            "reason": str(parsed.get("reason", "semantic assessment")),
        }
    except Exception as exc:  # Remote services must not make submissions fail.
        result = _local_semantic_signal(text)
        result["fallback_reason"] = type(exc).__name__
        return result


def stylometric_signal(text: str) -> dict[str, Any]:
    """Estimate AI-likelihood from structural writing statistics.

    Captures sentence-length rhythm, vocabulary diversity, and punctuation
    density. It intentionally cannot understand meaning, genre, or authorship.
    """

    words = _words(text)
    if not words:
        return {
            "ai_likelihood": 0.5,
            "metrics": {"sentence_length_cv": 0.0, "type_token_ratio": 0.0, "punctuation_density": 0.0},
        }

    lengths = _sentence_lengths(text)
    mean_length = statistics.mean(lengths)
    length_cv = (statistics.pstdev(lengths) / mean_length) if mean_length else 0.0
    type_token_ratio = len(set(words)) / len(words)
    punctuation_density = sum(char in ",;:!?" for char in text) / max(1, len(words))

    # AI-like structural evidence: low rhythm variation and a narrow but
    # polished vocabulary. High punctuation density is treated as human-like
    # only weakly because lists and generated text can both contain it.
    regularity = 1.0 - min(1.0, length_cv / 0.9)
    narrow_vocabulary = 1.0 - min(1.0, max(0.0, (type_token_ratio - 0.35) / 0.55))
    punctuation_effect = min(1.0, punctuation_density / 0.45)
    ai_likelihood = _clamp(0.58 * regularity + 0.32 * narrow_vocabulary + 0.10 * (1.0 - punctuation_effect))

    return {
        "ai_likelihood": ai_likelihood,
        "metrics": {
            "sentence_length_cv": round(length_cv, 4),
            "type_token_ratio": round(type_token_ratio, 4),
            "punctuation_density": round(punctuation_density, 4),
        },
    }


def combine_signals(semantic: dict[str, Any], stylometry: dict[str, Any]) -> dict[str, Any]:
    """Combine signals and map evidence strength/agreement to confidence."""

    semantic_score = float(semantic["ai_likelihood"])
    style_score = float(stylometry["ai_likelihood"])
    combined_ai = _clamp(0.6 * semantic_score + 0.4 * style_score)
    agreement = 1.0 - abs(semantic_score - style_score)
    direction_strength = abs(combined_ai - 0.5) * 2.0

    # Confidence is high only when the direction is strong and both signals
    # agree. A disagreement therefore creates a meaningful uncertain result.
    confidence = _clamp(0.55 * direction_strength + 0.45 * agreement)
    if combined_ai >= 0.70 and confidence >= 0.75:
        attribution = "likely_ai"
    elif combined_ai <= 0.30 and confidence >= 0.75:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    return {
        "ai_likelihood": combined_ai,
        "confidence": confidence,
        "attribution": attribution,
        "agreement": round(agreement, 4),
    }
