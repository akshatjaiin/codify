import json
import os
from pathlib import Path
from typing import Any

import requests


INCEPTION_URL = "https://api.inceptionlabs.ai/v1/chat/completions"


DEFAULT_RESPONSE_SCHEMA = {
    "name": "CodeLearningDiagnosis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "issue": {"type": "string"},
            "concept_gap": {"type": "string"},
            "suggestion": {"type": "string"},
            "fix_now": {"type": "string"},
            "learn_now": {"type": "string"},
            "practice_now": {"type": "string"},
            "confidence_adjustment": {"type": "integer", "minimum": -30, "maximum": 30},
        },
        "required": [
            "issue",
            "concept_gap",
            "suggestion",
            "fix_now",
            "learn_now",
            "practice_now",
            "confidence_adjustment",
        ],
    },
}


def _read_inception_key_from_dotenv() -> str:
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if not dotenv_path.exists():
        return ""

    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("INCEPTION_API_KEY="):
                continue

            value = line.split("=", 1)[1].strip()
            if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
                value = value[1:-1]
            return value.strip()
    except OSError:
        return ""

    return ""


def get_inception_api_key() -> str:
    env_key = os.environ.get("INCEPTION_API_KEY", "").strip()
    if env_key:
        return env_key

    dotenv_key = _read_inception_key_from_dotenv()
    if dotenv_key:
        os.environ["INCEPTION_API_KEY"] = dotenv_key
    return dotenv_key


def diagnose_with_inception(
    *,
    code: str,
    language: str,
    filename: str,
    concept_signals: dict[str, Any] | None = None,
    model: str = "mercury-2",
    timeout_seconds: int = 35,
) -> dict[str, Any]:
    api_key = get_inception_api_key()
    if not api_key:
        return {
            "ok": False,
            "error": "INCEPTION_API_KEY is not set in environment.",
        }

    concept_signals = concept_signals or {}
    prompt = {
        "task": "Act as a senior coding mentor for students. Focus on conceptual diagnosis, not just syntax errors.",
        "filename": filename,
        "language": language,
        "concept_signals": concept_signals,
        "code": code,
        "constraints": [
            "Be concise and specific.",
            "Use learner-friendly language.",
            "Return practical fix-now, learn-now, and practice-now actions.",
        ],
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an AI coding mentor that diagnoses conceptual misunderstandings in student code.",
            },
            {
                "role": "user",
                "content": json.dumps(prompt),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": DEFAULT_RESPONSE_SCHEMA,
        },
        "stream": False,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.post(INCEPTION_URL, headers=headers, json=payload, timeout=timeout_seconds)
        body = response.json()
    except requests.RequestException as error:
        return {"ok": False, "error": f"AI request failed: {error}"}
    except ValueError:
        return {"ok": False, "error": "AI service returned non-JSON response."}

    if response.status_code >= 400:
        return {
            "ok": False,
            "error": body.get("error", {}).get("message") if isinstance(body, dict) else "AI service error.",
            "status_code": response.status_code,
        }

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {
            "ok": False,
            "error": "Could not parse AI diagnosis payload.",
            "raw": body,
        }

    return {"ok": True, "diagnosis": parsed}


MERMAID_RESPONSE_SCHEMA = {
    "name": "MermaidFlowchart",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "mermaid_code": {"type": "string"},
        },
        "required": ["mermaid_code"],
    },
}


def generate_mermaid_flowchart(
    *,
    code: str,
    language: str,
    concept_signals: dict[str, Any] | None = None,
    model: str = "mercury-2",
    timeout_seconds: int = 40,
) -> dict[str, Any]:
    """Ask Inception AI to produce a Mermaid flowchart from the user's code."""
    api_key = get_inception_api_key()
    if not api_key:
        return {"ok": False, "error": "INCEPTION_API_KEY is not set in environment."}

    concept_signals = concept_signals or {}

    prompt = json.dumps({
        "task": (
            "Analyze this code and generate a Mermaid flowchart diagram showing its control flow. "
            "Include function definitions, loops, conditionals, returns, and key operations as nodes. "
            "Use `graph TD` (top-down) format. Make the diagram clear and educational. "
            "Only return the raw Mermaid code string—no markdown fences, no explanation."
        ),
        "language": language,
        "concept_signals_summary": {
            "loop_count": concept_signals.get("loop_count", 0),
            "conditional_count": concept_signals.get("conditional_count", 0),
            "function_like_count": concept_signals.get("function_like_count", 0),
            "max_loop_nesting": concept_signals.get("max_loop_nesting", 0),
        },
        "code": code,
    })

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a code visualization expert. You convert source code into clear, "
                    "educational Mermaid flowchart diagrams. Use descriptive node labels. "
                    "Use different shapes: stadiums for start/end, rectangles for operations, "
                    "diamonds for decisions, parallelograms for I/O."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": MERMAID_RESPONSE_SCHEMA,
        },
        "stream": False,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.post(INCEPTION_URL, headers=headers, json=payload, timeout=timeout_seconds)
        body = response.json()
    except requests.RequestException as error:
        return {"ok": False, "error": f"AI request failed: {error}"}
    except ValueError:
        return {"ok": False, "error": "AI service returned non-JSON response."}

    if response.status_code >= 400:
        return {
            "ok": False,
            "error": body.get("error", {}).get("message") if isinstance(body, dict) else "AI service error.",
        }

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
        mermaid_code = parsed.get("mermaid_code", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not parse AI flowchart payload."}

    if not mermaid_code.strip():
        return {"ok": False, "error": "AI returned empty flowchart."}

    return {"ok": True, "mermaid_code": mermaid_code}
