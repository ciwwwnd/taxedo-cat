import json
import logging

logger = logging.getLogger(__name__)


def parse_json_object(response_text: str) -> dict:
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.startswith("["):
        json.JSONDecoder().raw_decode(cleaned)
        raise ValueError("Model response must be a JSON object")
    object_start = cleaned.find("{")
    if object_start < 0:
        raise json.JSONDecodeError("No JSON object found", cleaned, 0)

    result, object_end = json.JSONDecoder().raw_decode(cleaned[object_start:])
    if not isinstance(result, dict):
        raise ValueError("Analysis response must be a JSON object")

    trailing = cleaned[object_start + object_end :].strip()
    if trailing.startswith("```"):
        trailing = trailing[3:].strip()
    if trailing:
        logger.warning("Ignored trailing text after Claude JSON response")
    return result

