import re

import spacy
from spacy.lang.de.stop_words import STOP_WORDS as _DE_STOP_WORDS
from spacy.lang.en.stop_words import STOP_WORDS as _EN_STOP_WORDS

_NER_MODELS = {"en": spacy.load("en_core_web_sm"), "de": spacy.load("de_core_news_sm")}
# Personal names identify the user; merchants and places are expense content.
_REDACT_LABELS = {"PERSON", "PER"}
_LANGUAGE_WORDS = {
    "en": (_EN_STOP_WORDS - _DE_STOP_WORDS) | {
        "receipt", "invoice", "total", "subtotal", "tax", "amount", "paid", "payment",
        "price", "qty", "quantity", "description", "due", "cash", "change",
    },
    "de": (_DE_STOP_WORDS - _EN_STOP_WORDS) | {
        "quittung", "rechnung", "summe", "zwischensumme", "mwst", "steuer", "betrag",
        "bezahlt", "zahlung", "kartenzahlung", "preis", "menge", "datum", "rückgeld",
        "zahlen", "gegeben", "kasse",
    },
}
# Separator lines added by document conversion are English in every receipt.
_CONVERSION_MARKER = re.compile(
    r"^(<!-- page \d+ -->|--- OCR reading \d+ of the SAME image .* ---)$"
)


def _language(text: str) -> str | None:
    """Return the clearly dominant language, or None when the text is ambiguous."""
    counts = dict.fromkeys(_LANGUAGE_WORDS, 0)
    for line in text.splitlines():
        if _CONVERSION_MARKER.match(line.strip()):
            continue
        # Words of one or two letters are mostly OCR noise.
        for word in re.findall(r"[^\W\d_]{3,}", line.lower()):
            for language, words in _LANGUAGE_WORDS.items():
                counts[language] += word in words
    (language, top), (_, runner_up) = sorted(counts.items(), key=lambda item: -item[1])
    return language if top >= 3 and top >= 3 * runner_up else None


def _redact_entities(line: str, models) -> str:
    spans = []
    for model in models:
        doc = model(line)
        for ent in doc.ents:
            if ent.label_ in _REDACT_LABELS:
                spans.append((ent.start_char, ent.end_char))
    if not spans:
        return line
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    result = []
    cursor = 0
    for start, end in merged:
        result.append(line[cursor:start])
        cursor = end
    result.append(line[cursor:])
    return "".join(result)


def expense_content(text: str) -> str:
    """Remove only personal names detected by the local NER models.

    A model for another language mistakes ordinary words for names, so only the
    text's own language model runs; both run when the language is unclear.
    """
    text = str(text)
    language = _language(text)
    models = [_NER_MODELS[language]] if language else list(_NER_MODELS.values())
    lines = []
    for line in text.splitlines():
        line = _redact_entities(line, models).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def safe_answers(answers):
    return tuple(
        {
            "question": expense_content(item.get("question", "")),
            "answer": expense_content(item.get("answer", "")),
        } for item in answers
    )
