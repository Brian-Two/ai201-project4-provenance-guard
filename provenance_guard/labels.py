"""Plain-language transparency labels."""

LABELS = {
    "likely_ai": (
        "This piece shows strong signs of AI-assisted writing. "
        "The signals are not proof of authorship; the creator can appeal this result."
    ),
    "uncertain": (
        "We couldn't confidently determine whether this piece was written by a person "
        "or generated with AI. Treat this result as uncertain; the creator can appeal."
    ),
    "likely_human": (
        "This piece shows strong signs of human writing. Detection is not proof of authorship, "
        "but the signals support this result."
    ),
}


def transparency_label(attribution: str) -> str:
    return LABELS.get(attribution, LABELS["uncertain"])

