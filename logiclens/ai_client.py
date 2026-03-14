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
    failed_attempts: int = 0,
    memory_context: str = "",
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
        "pedagogical_state": {
            "failed_attempts": failed_attempts,
            "instruction": "If failed_attempts > 2, the student is stuck. Give a smaller progressive hint, do NOT give the direct answer. Focus strictly on underlying conceptual misunderstanding."
        },
        "student_history": memory_context or "No prior history available.",
        "constraints": [
            "Be concise and specific.",
            "Use learner-friendly language.",
            "Never write the direct code solution for them.",
            "Return practical fix-now, learn-now, and practice-now actions.",
            "If student_history shows a RECURRING pattern, mention it explicitly in your diagnosis — e.g. 'You've struggled with nested loops before.'",
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
                    "diamonds for decisions, parallelograms for I/O. "
                    "CRITICAL: Do NOT use quotes (\", '), parentheses, brackets, or braces inside the node labels. Keep node text clean and simple to prevent Mermaid syntax errors (e.g., use A[print hi] instead of A[print(\"hi\")])."
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

    # Sanitize the Mermaid code to prevent parse errors.
    # The AI sometimes ignores instructions and includes characters like (), "", or {} inside node labels [like this].
    # Mermaid crashes if it sees unquoted special characters inside brackets.
    import re
    def sanitize_label(match):
        content = match.group(1)
        # Remove problematic characters from the label text
        clean = re.sub(r'["\'\(\)\{\}\[\]]', '', content)
        return f"[{clean}]"

    # Find everything inside [ ] and sanitize it
    sanitized_code = re.sub(r'\[(.*?)\]', sanitize_label, mermaid_code)

    return {"ok": True, "mermaid_code": sanitized_code}


YOUTUBE_QUERY_SCHEMA = {
    "name": "YouTubeSearchQueries",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of 3 to 5 highly specific YouTube search queries."
            }
        },
        "required": ["queries"]
    }
}

def generate_youtube_queries(concepts: list[str], model: str = "mercury-2", timeout_seconds: int = 15) -> dict[str, Any]:
    """Ask Inception AI to generate targeted YouTube search queries based on weak concepts."""
    api_key = get_inception_api_key()
    if not api_key:
        return {"ok": False, "error": "INCEPTION_API_KEY is not set."}

    if not concepts:
        concepts = ["general programming structure"]

    prompt = {
        "task": (
            "Generate 3 to 5 highly specific, high-quality YouTube search queries to help "
            "a student learn the following coding concepts. "
            "The queries should be phrased exactly as someone would type them into YouTube to find the best tutorials."
        ),
        "concepts": concepts
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert programming educator who knows exactly how to search YouTube for the best coding tutorials."
            },
            {"role": "user", "content": json.dumps(prompt)}
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": YOUTUBE_QUERY_SCHEMA
        },
        "stream": False
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
        queries = parsed.get("queries", [])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not parse AI queries payload."}

    return {"ok": True, "queries": queries}


RUBBER_DUCK_RESPONSE_SCHEMA = {
    "name": "RubberDuckReply",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "One short Socratic response. Prefer a probing question, no direct answer.",
            }
        },
        "required": ["reply"],
    },
}


def rubber_duck_chat(
    *,
    code: str,
    language: str,
    history: list[dict],
    user_message: str,
    memory_context: str = "",
    code_context: dict[str, Any] | None = None,
    failed_attempts: int = 0,
    model: str = "mercury-2",
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """
    Rubber Duck Mode: Socratic AI mentor.
    Listens to student explain their code, catches gaps in reasoning.
    NEVER gives the answer. Only asks probing questions.
    """
    api_key = get_inception_api_key()
    if not api_key:
        return {"ok": False, "error": "INCEPTION_API_KEY is not set."}

    code_context = code_context or {}

    system_prompt = f"""You are a Rubber Duck Debugger — a Socratic coding mentor for students.

The student is explaining their code to you, line by line or concept by concept.

Your STRICT rules:
1. NEVER reveal direct answers, fixed code, pseudocode, or code snippets.
2. Listen carefully. When the student says something LOGICALLY INCORRECT or reveals a CONCEPTUAL GAP, ask ONE sharp, focused probing question that forces them to think.
3. If everything sounds correct, acknowledge briefly and ask them to continue ("I see — and what happens next?").
4. Ask only ONE question at a time. Never lecture. Never explain. Just question.
5. Be brief. Max 2-3 sentences. Act confused so the student has to be precise.
6. If the student seems stuck for 2+ replies, ask a simpler version of the question.
7. Track their explanation against the real code logic below.
8. Build intuition, not answers: focus on invariants, state transitions, edge cases, and complexity reasoning.
9. Do NOT output any line of code from the user's program.
10. Use code context every turn: ground your question in at least one concrete structural anchor (e.g., loop, branch, function role, variable purpose, or data-flow step).

The code they are explaining:
```{language}
{code}
```

AST/context summary for guidance:
{json.dumps(code_context)}

Student learning memory context:
{memory_context or "No prior memory context."}

Estimated stuck signal: failed_attempts={failed_attempts}

You are an impartial rubber duck. You do not know the answer — you only know if the student's explanation is internally consistent.
When their explanation contradicts the code's actual behavior, ask a probing question that helps them self-correct without giving implementation details."""

    messages = [{"role": "system", "content": system_prompt}]

    # Inject history
    for turn in history[-10:]:
        role = str(turn.get("role", "user")).strip().lower()
        if role == "duck":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue
        content = str(turn.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": RUBBER_DUCK_RESPONSE_SCHEMA,
        },
        "stream": False,
        "max_tokens": 200,
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
        reply = str(parsed.get("reply", "")).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not parse AI response."}

    if not reply:
        return {"ok": False, "error": "AI returned an empty rubber duck reply."}

    return {"ok": True, "reply": reply}


MORPH_RESPONSE_SCHEMA = {
    "name": "MorphedCode",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "morphed_code": {
                "type": "string",
                "description": "The fully optimized, clean version of the code. Must be complete and runnable."
            },
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short label, e.g. 'O(N²) → O(N)' or 'Naming'"},
                        "detail": {"type": "string", "description": "One sentence explaining the improvement."}
                    },
                    "required": ["label", "detail"]
                },
                "description": "2-5 specific improvements made. Be precise."
            },
            "complexity_before": {"type": "string", "description": "e.g. O(N²)"},
            "complexity_after":  {"type": "string", "description": "e.g. O(N)"}
        },
        "required": ["morphed_code", "changes", "complexity_before", "complexity_after"]
    }
}


def morph_code(
    *,
    code: str,
    language: str,
    concept_signals: dict[str, Any] | None = None,
    inefficiency_patterns: list[str] | None = None,
    memory_context: str = "",
    model: str = "mercury-2",
    timeout_seconds: int = 40,
) -> dict[str, Any]:
    """
    Live Code Morphing Optimizer.
    Takes student code and returns a semantically optimized version
    with a precise changelog of every improvement made.
    """
    api_key = get_inception_api_key()
    if not api_key:
        return {"ok": False, "error": "INCEPTION_API_KEY is not set."}

    concept_signals = concept_signals or {}
    inefficiency_patterns = inefficiency_patterns or []

    prompt = {
        "task": (
            "You are a senior software engineer performing a code review and optimization. "
            "Rewrite the provided code to be optimally clean, efficient, and Pythonic (or idiomatic for the language). "
            "Fix inefficiencies like: nested loops that can be sets/dicts, magic numbers, poor naming, "
            "missing edge case guards, redundant recomputation, and structural anti-patterns. "
            "IMPORTANT: Return the FULL rewritten code in morphed_code. Do NOT return partial snippets. "
            "List every specific change in the changes array."
        ),
        "language": language,
        "concept_signals_summary": {
            "confidence_score": concept_signals.get("confidence_score", "N/A"),
            "issue": concept_signals.get("issue", ""),
            "loop_count": concept_signals.get("loop_count", 0),
            "max_loop_nesting": concept_signals.get("max_loop_nesting", 0),
        },
        "detected_inefficiency_patterns": inefficiency_patterns,
        "student_memory_context": memory_context or "No prior student memory context available.",
        "code": code,
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an elite code optimizer. You transform student code into clean, efficient, idiomatic code. "
                    "You preserve the intent and logic but eliminate ALL inefficiencies. "
                    "You return structured JSON with the full rewritten code and a precise diff changelog."
                ),
            },
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": MORPH_RESPONSE_SCHEMA,
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
        return {"ok": False, "error": "AI returned non-JSON response."}

    if response.status_code >= 400:
        return {
            "ok": False,
            "error": body.get("error", {}).get("message") if isinstance(body, dict) else "AI service error.",
        }

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not parse AI morph payload."}

    return {
        "ok": True,
        "morphed_code": parsed.get("morphed_code", ""),
        "changes": parsed.get("changes", []),
        "complexity_before": parsed.get("complexity_before", "?"),
        "complexity_after": parsed.get("complexity_after", "?"),
    }
