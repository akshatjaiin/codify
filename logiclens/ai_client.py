import json
import os
import re
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

    editor_focus = (code_context or {}).get("editor_focus") or {}
    selected_text = str(editor_focus.get("selected_text", "")).strip()
    selected_preview = selected_text[:180] if selected_text else ""
    focus_line = code_context.get("editor_focus_line")
    focus_line_text = str(code_context.get("editor_focus_line_text", "")).strip()
    focus_neighborhood = code_context.get("editor_focus_neighborhood") or []
    user_message_lower = (user_message or "").strip().lower()
    explain_mode = bool(re.search(r"\b(explain|samjha|samjhao|meaning|matlab)\b", user_message_lower))
    requested_line = None
    if "first line" in user_message_lower:
        requested_line = 1
    else:
        line_match = re.search(r"\bline\s*(\d+)\b|\b(\d+)\s*line\b", user_message_lower)
        if line_match:
            requested_line = int(line_match.group(1) or line_match.group(2))

    system_prompt = """You are a helpful Rubber Duck Debugger for code intuition.

Rules:
- Never provide complete code, direct fix, or step-by-step solution.
- Default mode: ask one focused Socratic question at a time.
- Keep it short: max 2-3 sentences.
- Ground each question in the user's current code context.
- Always anchor your question to the focused line or selected snippet when available.
- Never ask the user to point to a function/section/line because focus context is already provided.
- If user asks for direct code, refuse politely and ask a conceptual question.
- Prioritize intuition: invariants, state transitions, edge cases, and complexity.
- You ALWAYS have access to the user's full current code in system context for this turn.
- NEVER say you cannot see code, cannot access files, or ask the user to paste code again.
- If user asks to explain a line/first line/line number: switch to TEACHER MODE.
    In TEACHER MODE, first give a concrete explanation of the focused line in plain language, then ask one short check question.
- Do not guess random operations. If focus line text is empty, use nearest non-empty focus line from context.
"""

    context_payload = {
        "language": language,
        "failed_attempts": failed_attempts,
        "teaching_mode": {
            "explain_mode": explain_mode,
            "requested_line": requested_line,
        },
        "editor_focus": {
            "cursor_line": editor_focus.get("cursor_line"),
            "cursor_column": editor_focus.get("cursor_column"),
            "selection_start_line": editor_focus.get("selection_start_line"),
            "selection_end_line": editor_focus.get("selection_end_line"),
            "selected_text_preview": selected_preview or "None",
            "focus_line": focus_line,
            "focus_line_text": focus_line_text or "None",
            "focus_neighborhood": focus_neighborhood,
        },
        "memory_context": memory_context or "No prior memory context.",
        "code_context": code_context,
        "current_code": code,
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": "Current code + learner context (use for grounding, never output code):\n"
            + json.dumps(context_payload, ensure_ascii=False),
        },
        {
            "role": "system",
            "content": f"FULL_CURRENT_CODE ({language})\n---BEGIN CODE---\n{code}\n---END CODE---",
        },
    ]

    def _format_focus_meta(focus_obj: dict[str, Any] | None) -> str:
        if not isinstance(focus_obj, dict):
            return ""
        cursor_line = focus_obj.get("cursor_line")
        cursor_column = focus_obj.get("cursor_column")
        selection_start = focus_obj.get("selection_start_line")
        selection_end = focus_obj.get("selection_end_line")
        selected_text = str(focus_obj.get("selected_text") or "").strip()
        if len(selected_text) > 120:
            selected_text = selected_text[:120]
        parts = []
        if cursor_line:
            if cursor_column:
                parts.append(f"cursor=L{cursor_line}:C{cursor_column}")
            else:
                parts.append(f"cursor=L{cursor_line}")
        if selection_start and selection_end:
            parts.append(f"selection=L{selection_start}-L{selection_end}")
        if selected_text:
            parts.append(f"selected='{selected_text}'")
        if not parts:
            return ""
        return "[user_focus " + " | ".join(parts) + "]"

    # Inject history
    for turn in history[-10:]:
        role = str(turn.get("role", "user")).strip().lower()
        if role == "duck":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue
        content = str(turn.get("content", "")).strip()
        if not content:
            continue

        if role == "user":
            focus_prefix = _format_focus_meta(turn.get("focus"))
            if focus_prefix:
                messages.append({"role": role, "content": f"{focus_prefix}\n{content}"})
            else:
                messages.append({"role": role, "content": content})
        else:
            messages.append({"role": role, "content": content})

    current_focus_prefix = _format_focus_meta(editor_focus)
    if current_focus_prefix:
        messages.append({"role": "user", "content": f"{current_focus_prefix}\n{user_message}"})
    else:
        messages.append({"role": "user", "content": user_message})

    payload = {
        "model": model,
        "messages": messages,
        "reasoning_effort": "instant",
        "stream": False,
        "max_tokens": 220,
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
        content = body["choices"][0]["message"].get("content", "")
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    text_val = item.get("text") or item.get("content")
                    if text_val:
                        chunks.append(str(text_val))
                elif isinstance(item, str):
                    chunks.append(item)
            reply = "\n".join(chunks).strip()
        else:
            reply = str(content or "").strip()
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": "Could not parse AI response."}

    if reply.startswith("{"):
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict) and "reply" in parsed:
                reply = str(parsed.get("reply", "")).strip()
        except json.JSONDecodeError:
            pass

    if not reply:
        return {"ok": False, "error": "AI returned an empty rubber duck reply."}

    missing_code_context = re.search(
        r"(don't|do not|cannot|can't)\s+(have|access|see).*(code|source|file)|share\s+the\s+snippet|paste\s+the\s+code",
        reply,
        re.IGNORECASE,
    )
    generic_location_ask = re.search(
        r"which\s+specific\s+(function|section|line)|point\s+me\s+to|which\s+line\s+you\s+are\s+referring",
        reply,
        re.IGNORECASE,
    )
    random_guessy_reply = re.search(
        r"define\s+the\s+function\s+signature|import\s+a\s+module|set\s+an\s+initial\s+variable",
        reply,
        re.IGNORECASE,
    )
    if missing_code_context or generic_location_ask:
        anchor = "your current focus"
        if focus_line and focus_line_text:
            anchor = f"line {focus_line} ({focus_line_text[:90]})"
        elif selected_preview:
            anchor = f"the selected snippet ({selected_preview[:90]})"

        reply = (
            f"I can see your code context. At {anchor}, what state change should happen after this step, "
            "and which assumption might be causing the mismatch you are seeing?"
        )

    if random_guessy_reply and focus_line_text:
        reply = (
            f"On line {focus_line}, this part `{focus_line_text[:100]}` runs at this step of your flow. "
            "In your own words, what input/state does it consume, and what output/state should it produce next?"
        )

    if explain_mode and focus_line_text:
        line_ref = requested_line or focus_line
        reply = (
            f"Line {line_ref} is doing: `{focus_line_text[:120]}`. "
            "It matters because it changes/sets the state used by the next step. "
            "Quick check: what value do you expect immediately after this line executes?"
        )

    code_like = re.search(r"```|\b(def|class|function|return|for|while|if|else|import|public|static)\b\s*[:\(\{]", reply, re.IGNORECASE)
    if code_like:
        reply = (
            "Let’s keep this intuition-first. At your current focus area, what should the variable state be "
            "before and after this step, and which edge case could break that assumption?"
        )

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
