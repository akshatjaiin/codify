import json
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from .ai_client import diagnose_with_inception, generate_mermaid_flowchart, get_inception_api_key, generate_youtube_queries
from .models import DiagnosticSnapshot, LearnerAction, UserProfile, KnowledgeEntry, CreditTransaction, Note

try:
    import jsbeautifier
except ImportError:
    jsbeautifier = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from tree_sitter import Parser as TreeSitterParser
except ImportError:
    TreeSitterParser = None

_tsl_get_parser = None
_tsl_get_language = None
_tslp_get_parser = None
_tslp_get_language = None

try:
    from tree_sitter_languages import get_parser as _tsl_get_parser
    from tree_sitter_languages import get_language as _tsl_get_language
except ImportError:
    pass

try:
    _ts_pack = importlib.import_module("tree_sitter_language_pack")
    _tslp_get_parser = getattr(_ts_pack, "get_parser", None)
    _tslp_get_language = getattr(_ts_pack, "get_language", None)
except ImportError:
    pass


# --- YouTube Recommendation Database ---
YOUTUBE_RECOMMENDATIONS = {
    "complexity-optimization": [
        {"title": "Big O Notation — Full Course", "url": "https://www.youtube.com/watch?v=Mo4vesaut8g", "channel": "freeCodeCamp"},
        {"title": "Time Complexity Analysis", "url": "https://www.youtube.com/watch?v=9TlHvipP5yA", "channel": "Abdul Bari"},
        {"title": "Hash Tables Explained", "url": "https://www.youtube.com/watch?v=KyUTuwz_b7Q", "channel": "CS Dojo"},
    ],
    "branching-decomposition": [
        {"title": "Clean Code — Functions", "url": "https://www.youtube.com/watch?v=7EmboKQH8lM", "channel": "Uncle Bob"},
        {"title": "Guard Clauses & Early Returns", "url": "https://www.youtube.com/watch?v=EumXak7TyQ0", "channel": "Web Dev Simplified"},
        {"title": "Refactoring If-Else Chains", "url": "https://www.youtube.com/watch?v=lQ_rGCL17EE", "channel": "CodeAesthetic"},
    ],
    "modularization": [
        {"title": "Single Responsibility Principle", "url": "https://www.youtube.com/watch?v=UQqY3_6Epbg", "channel": "Fireship"},
        {"title": "Functions & Decomposition", "url": "https://www.youtube.com/watch?v=yatgY4NpZXE", "channel": "CS50"},
        {"title": "Code Smells & Refactoring", "url": "https://www.youtube.com/watch?v=D4auWwMsEnY", "channel": "CodeAesthetic"},
    ],
    "general-structure": [
        {"title": "Data Structures for Beginners", "url": "https://www.youtube.com/watch?v=RBSGKlAvoiM", "channel": "freeCodeCamp"},
        {"title": "How to Write Clean Code", "url": "https://www.youtube.com/watch?v=UjhX2sVf0eg", "channel": "Traversy Media"},
        {"title": "Python Best Practices", "url": "https://www.youtube.com/watch?v=C-gEQdGVXbk", "channel": "Tech With Tim"},
    ],
}


def _get_or_create_profile(request):
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key
    profile, _ = UserProfile.objects.get_or_create(session_key=session_key)
    return profile


def _update_knowledge(profile, concept_tag, confidence_score):
    if not concept_tag or concept_tag == "unknown":
        return
    if confidence_score >= 80:
        level = "strong"
    elif confidence_score >= 50:
        level = "developing"
    else:
        level = "beginner"

    entry, created = KnowledgeEntry.objects.get_or_create(
        user=profile,
        concept_tag=concept_tag,
        defaults={"proficiency_level": level, "practice_count": 1},
    )
    if not created:
        entry.practice_count += 1
        entry.proficiency_level = level
        entry.last_practiced = timezone.now()
        entry.save()


def _grant_credits(profile, amount, reason):
    profile.credits += amount
    profile.save()
    CreditTransaction.objects.create(user=profile, amount=amount, reason=reason)


def _use_credits(profile, amount, reason):
    if profile.credits < amount:
        return False
    profile.credits -= amount
    profile.save()
    CreditTransaction.objects.create(user=profile, amount=-amount, reason=reason)
    return True


# --- Tree-sitter helpers (unchanged) ---

def _tree_sitter_backend_available():
    has_direct_parser = callable(_tsl_get_parser) or callable(_tslp_get_parser)
    has_language_and_parser = TreeSitterParser is not None and (
        callable(_tsl_get_language) or callable(_tslp_get_language)
    )
    return has_direct_parser or has_language_and_parser


def _get_tree_sitter_parser(language_name):
    last_error = None

    for parser_builder in (_tsl_get_parser, _tslp_get_parser):
        if not callable(parser_builder):
            continue
        try:
            return parser_builder(language_name)
        except Exception as error:
            last_error = error

    if TreeSitterParser is None:
        if last_error:
            raise RuntimeError(str(last_error))
        raise RuntimeError("tree_sitter.Parser is unavailable.")

    language_obj = None
    for language_builder in (_tsl_get_language, _tslp_get_language):
        if not callable(language_builder):
            continue
        try:
            language_obj = language_builder(language_name)
            if language_obj is not None:
                break
        except Exception as error:
            last_error = error

    if language_obj is None:
        if last_error:
            raise RuntimeError(str(last_error))
        raise RuntimeError(f"No tree-sitter language provider for '{language_name}'.")

    try:
        return TreeSitterParser(language_obj)
    except TypeError:
        parser = TreeSitterParser()
        if hasattr(parser, "set_language"):
            parser.set_language(language_obj)
        else:
            parser.language = language_obj
        return parser


# --- Page Views ---

@ensure_csrf_cookie
def dashboard_page(request):
    _get_or_create_profile(request)
    return render(request, "dashboard.html")


@ensure_csrf_cookie
def editor_page(request):
    _get_or_create_profile(request)
    return render(request, "editor.html")


@ensure_csrf_cookie
def progress_page(request):
    _get_or_create_profile(request)
    return render(request, "progress.html")


@ensure_csrf_cookie
def knowledge_page(request):
    _get_or_create_profile(request)
    return render(request, "knowledge.html")


def analysis_page(request):
    filename = request.GET.get("filename", "untitled")
    language = request.GET.get("language", "plaintext")
    return render(request, "analysis.html", {"filename": filename, "language": language})


def analyze_api_dummy(request):
    return JsonResponse(
        {
            "ok": True,
            "issue": "Nested loops causing O(n²) complexity",
            "concept_gap": "Hash map optimization",
            "suggestion": "Use dictionary lookup to reduce complexity to O(n)",
            "note": "Dummy response. Replace with real analysis pipeline."
        }
    )


# --- Process helpers (unchanged) ---

def _run_process(command, cwd=None, stdin_text="", timeout=5):
    result = subprocess.run(
        command,
        input=stdin_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=cwd,
        timeout=timeout,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def _format_with_command(command, code, timeout=5):
    result = subprocess.run(
        command,
        input=code,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return result


def _format_response(tool, formatted_code, original_code):
    normalized_formatted = formatted_code if formatted_code is not None else ""
    normalized_original = original_code if original_code is not None else ""
    return {
        "ok": True,
        "tool": tool,
        "formatted_code": normalized_formatted,
        "changed": normalized_formatted != normalized_original,
    }


def _is_command_available(command_name):
    return shutil.which(command_name) is not None


def _find_executable(command_names, explicit_paths=None):
    if isinstance(command_names, str):
        command_names = [command_names]
    if explicit_paths is None:
        explicit_paths = []

    for command_name in command_names:
        resolved = shutil.which(command_name)
        if resolved:
            return resolved

    for candidate in explicit_paths:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.exists() and candidate_path.is_file():
            return str(candidate_path)

    return None


def _find_clang_format():
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LocalAppData", "")

    explicit_candidates = [
        Path(program_files) / "LLVM" / "bin" / "clang-format.exe",
        Path(program_files_x86) / "LLVM" / "bin" / "clang-format.exe",
    ]

    vs_roots = [
        Path(program_files) / "Microsoft Visual Studio",
        Path(program_files_x86) / "Microsoft Visual Studio",
    ]
    for root in vs_roots:
        if root.exists():
            explicit_candidates.extend(root.glob("**/VC/Tools/Llvm/**/clang-format.exe"))

    if local_app_data:
        vscode_llvm = Path(local_app_data) / "Programs" / "LLVM" / "bin" / "clang-format.exe"
        explicit_candidates.append(vscode_llvm)

    return _find_executable(["clang-format", "clang-format.exe"], [str(path) for path in explicit_candidates])


def _resolve_google_java_format_command():
    google_format_exe = _find_executable(["google-java-format", "google-java-format.exe", "google-java-format.bat", "google-java-format.cmd"])
    if google_format_exe:
        return [google_format_exe, "-"]

    java_exe = _find_executable(["java", "java.exe"])
    if not java_exe:
        return None

    project_root = Path(__file__).resolve().parent.parent
    jar_candidates = []
    for pattern in (
        "google-java-format*.jar",
        "tools/google-java-format*.jar",
        "bin/google-java-format*.jar",
        "vendor/google-java-format*.jar",
    ):
        jar_candidates.extend(project_root.glob(pattern))

    if not jar_candidates:
        return None

    newest_jar = max(jar_candidates, key=lambda path: path.stat().st_mtime)
    return [java_exe, "-jar", str(newest_jar), "-"]


def _beautify_with_jsbeautifier(language, code):
    if jsbeautifier is None:
        return None

    options = jsbeautifier.default_options()
    options.indent_size = 4

    if language == "javascript" and hasattr(jsbeautifier, "beautify"):
        return jsbeautifier.beautify(code, options)

    if language == "html":
        if hasattr(jsbeautifier, "beautify_html"):
            return jsbeautifier.beautify_html(code, options)
        try:
            html_module = importlib.import_module("jsbeautifier.html")
            if hasattr(html_module, "beautify"):
                return html_module.beautify(code, options)
        except Exception:
            pass

        if BeautifulSoup is not None:
            try:
                return BeautifulSoup(code, "html.parser").prettify()
            except Exception:
                return None

    if language == "css":
        if hasattr(jsbeautifier, "beautify_css"):
            return jsbeautifier.beautify_css(code, options)
        try:
            css_module = importlib.import_module("jsbeautifier.css")
            if hasattr(css_module, "beautify"):
                return css_module.beautify(code, options)
        except Exception:
            try:
                cssbeautifier_module = importlib.import_module("cssbeautifier")
                if hasattr(cssbeautifier_module, "beautify"):
                    return cssbeautifier_module.beautify(code, options)
            except Exception:
                return None

    return None


def _tree_sitter_language_name(language):
    mapping = {
        "python": "python",
        "javascript": "javascript",
        "typescript": "typescript",
        "java": "java",
        "c": "c",
        "cpp": "cpp",
        "html": "html",
        "css": "css",
        "json": "json",
    }
    return mapping.get(language)


def _serialize_ast_node(node, code_bytes, depth=0, max_depth=6, max_children=40):
    entry = {
        "type": node.type,
        "start": {"row": node.start_point[0], "column": node.start_point[1]},
        "end": {"row": node.end_point[0], "column": node.end_point[1]},
        "named": bool(node.is_named),
    }

    if depth >= max_depth:
        entry["truncated"] = True
        return entry

    children = []
    child_count = node.child_count
    take_count = min(child_count, max_children)

    for index in range(take_count):
        child = node.children[index]
        children.append(
            _serialize_ast_node(
                child,
                code_bytes,
                depth=depth + 1,
                max_depth=max_depth,
                max_children=max_children,
            )
        )

    if child_count > max_children:
        children.append({"type": "...", "truncated_children": child_count - max_children})

    if children:
        entry["children"] = children
    else:
        snippet_bytes = code_bytes[node.start_byte:node.end_byte]
        snippet = snippet_bytes.decode("utf-8", errors="replace").strip()
        if len(snippet) > 120:
            snippet = f"{snippet[:117]}..."
        entry["snippet"] = snippet

    return entry


def _collect_concept_signals(node):
    loop_keywords = ("for", "while", "loop", "foreach")
    conditional_keywords = ("if", "else", "switch", "case", "ternary")
    function_keywords = ("function", "method", "lambda", "arrow", "def", "declaration")

    max_depth = 0
    loop_count = 0
    conditional_count = 0
    function_count = 0
    max_loop_nesting = 0

    stack = [(node, 0, 0)]
    while stack:
        current, depth, loop_nesting = stack.pop()
        node_type = str(current.type).lower()

        if depth > max_depth:
            max_depth = depth

        is_loop = any(keyword in node_type for keyword in loop_keywords)
        if is_loop:
            loop_count += 1
            loop_nesting += 1
            if loop_nesting > max_loop_nesting:
                max_loop_nesting = loop_nesting

        if any(keyword in node_type for keyword in conditional_keywords):
            conditional_count += 1

        if any(keyword in node_type for keyword in function_keywords):
            function_count += 1

        for child in reversed(current.children):
            stack.append((child, depth + 1, loop_nesting))

    concept_gap = "No major structural gap detected from AST."
    suggestion = "Proceed to complexity and naming checks for deeper diagnostics."
    issue = "Structure appears manageable."
    concept_tag = "general-structure"
    fix_now = "Run one simplification pass: rename unclear symbols and split long blocks into helper functions."
    learn_now = "Review how readable structure reduces bug rate and improves debugging speed."
    practice_now = "Refactor one function into two smaller functions with clear names."

    if max_loop_nesting >= 2:
        issue = "Nested loops detected (possible high time complexity)."
        concept_gap = "Iteration strategy and lookup optimization may be weak."
        suggestion = "Try hash-based lookup or preprocessing to reduce nested iteration."
        concept_tag = "complexity-optimization"
        fix_now = "Replace one inner loop with a dictionary/set lookup where possible."
        learn_now = "Study time complexity contrast between O(n²) nested loops and O(n) hash lookup patterns."
        practice_now = "Solve one duplicate-detection problem using a set instead of double loop."
    elif conditional_count >= 3 and max_loop_nesting >= 1:
        issue = "High conditionals inside loops detected (Control Flow Overload)."
        concept_gap = "Mixing iteration and complex business logic violates Separation of Concerns."
        suggestion = "Extract the inner loop logic into a separate filtering or processing function."
        concept_tag = "control-flow-overload"
        fix_now = "Move the 'if/else' block from inside your loop into a standalone 'process_item()' function."
        learn_now = "Review declarative processing modes like map/filter vs imperative nested loops."
        practice_now = "Refactor a loop with multiple if-statements into a pipeline of small list comprehensions."
    elif conditional_count >= 6:
        issue = "High conditional branching detected."
        concept_gap = "Decision logic decomposition may be unclear."
        suggestion = "Extract helper functions and reduce branching depth for readability."
        concept_tag = "branching-decomposition"
        fix_now = "Extract at least one branch into a helper function with a descriptive name."
        learn_now = "Learn guard clauses and early return patterns to flatten nested conditions."
        practice_now = "Refactor one if-else-heavy solution using helper predicates."
    elif max_depth >= 18:
        issue = "Deep syntax tree depth detected (complex structure)."
        concept_gap = "Code decomposition and modularization may need improvement."
        suggestion = "Split logic into smaller functions and simplify control flow."
        concept_tag = "modularization"
        fix_now = "Break the deepest block into smaller functions each doing one task."
        learn_now = "Review single-responsibility principle and function cohesion basics."
        practice_now = "Take one long function and split into input-process-output helpers."

    penalty = 0
    penalty += min(max_loop_nesting * 15, 45)
    penalty += min(max(conditional_count - 3, 0) * 4, 24)
    penalty += min(max(max_depth - 10, 0) * 2, 20)
    confidence_score = max(0, 100 - penalty)

    if confidence_score >= 80:
        confidence_label = "Strong"
    elif confidence_score >= 60:
        confidence_label = "Moderate"
    elif confidence_score >= 40:
        confidence_label = "Developing"
    else:
        confidence_label = "Needs Reinforcement"

    return {
        "loop_count": loop_count,
        "conditional_count": conditional_count,
        "function_like_count": function_count,
        "max_tree_depth": max_depth,
        "max_loop_nesting": max_loop_nesting,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "issue": issue,
        "concept_gap": concept_gap,
        "suggestion": suggestion,
        "concept_tag": concept_tag,
        "fix_now": fix_now,
        "learn_now": learn_now,
        "practice_now": practice_now,
    }


def _save_snapshot(filename, language, code, concept_signals, profile=None):
    return DiagnosticSnapshot.objects.create(
        profile=profile,
        filename=filename or "untitled",
        language=language,
        code=code,
        issue=concept_signals.get("issue", ""),
        concept_gap=concept_signals.get("concept_gap", ""),
        suggestion=concept_signals.get("suggestion", ""),
        concept_tag=concept_signals.get("concept_tag", ""),
        confidence_score=int(concept_signals.get("confidence_score", 0)),
        confidence_label=concept_signals.get("confidence_label", ""),
        loop_count=int(concept_signals.get("loop_count", 0)),
        conditional_count=int(concept_signals.get("conditional_count", 0)),
        function_like_count=int(concept_signals.get("function_like_count", 0)),
        max_tree_depth=int(concept_signals.get("max_tree_depth", 0)),
        max_loop_nesting=int(concept_signals.get("max_loop_nesting", 0)),
        fix_now=concept_signals.get("fix_now", ""),
        learn_now=concept_signals.get("learn_now", ""),
        practice_now=concept_signals.get("practice_now", ""),
    )


def _build_progress_payload(language="", limit=20):
    queryset = DiagnosticSnapshot.objects.all().order_by("-created_at")
    if language:
        queryset = queryset.filter(language=language)
    snapshots = list(queryset[:limit])

    timeline = []
    concept_counts = {}
    for snapshot in snapshots:
        concept_tag = snapshot.concept_tag or "unknown"
        concept_counts[concept_tag] = concept_counts.get(concept_tag, 0) + 1
        timeline.append(
            {
                "id": snapshot.id,
                "created_at": snapshot.created_at.isoformat(),
                "filename": snapshot.filename,
                "language": snapshot.language,
                "confidence_score": snapshot.confidence_score,
                "confidence_label": snapshot.confidence_label,
                "issue": snapshot.issue,
                "concept_gap": snapshot.concept_gap,
                "concept_tag": snapshot.concept_tag,
            }
        )

    timeline.reverse()
    average_confidence = 0
    if timeline:
        average_confidence = round(
            sum(item["confidence_score"] for item in timeline if isinstance(item["confidence_score"], int)) / len(timeline),
            2,
        )

    return {
        "timeline": timeline,
        "average_confidence": average_confidence,
        "attempt_count": len(timeline),
        "concept_counts": concept_counts,
    }


def _analyze_ast_complexity(node, code_bytes):
    """Analyze AST to find cognitive complexity hotspots."""
    hotspots = []
    
    def traverse(n):
        if n.type in ['function_definition', 'method_definition', 'class_definition', 'arrow_function']:
            complexity = 1
            name = "anonymous"
            for child in n.children:
                if child.type == 'identifier':
                    name = code_bytes[child.start_byte:child.end_byte].decode('utf-8', errors='ignore')
                    break
            
            def count_branches(inner_node):
                c = 0
                if inner_node.type in ['if_statement', 'for_statement', 'while_statement', 'catch_clause', 'switch_statement', 'elif_clause', 'else_clause']:
                    c += 1
                for child in inner_node.children:
                    c += count_branches(child)
                return c
            
            branches = count_branches(n)
            complexity += branches
            
            if complexity > 2:
                hotspots.append({
                    "name": name,
                    "type": n.type,
                    "line": n.start_point[0] + 1,
                    "complexity": complexity,
                    "status": "high" if complexity >= 10 else ("medium" if complexity >= 5 else "low")
                })
                
        for child in n.children:
            traverse(child)
            
    traverse(node)
    hotspots.sort(key=lambda x: x['complexity'], reverse=True)
    return hotspots


# --- API Endpoints ---

@csrf_exempt
def ast_tree_local(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if not _tree_sitter_backend_available():
        return JsonResponse(
            {
                "ok": False,
                "error": "Tree-sitter backend is not installed. Install dependencies from requirements.txt.",
            },
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    language = str(payload.get("language", "")).strip().lower()
    code = str(payload.get("code", ""))
    filename = str(payload.get("filename", "untitled")).strip() or "untitled"
    live_preview = bool(payload.get("live_preview", False))
    max_depth = min(max(int(payload.get("max_depth", 6)), 1), 12)
    max_children = min(max(int(payload.get("max_children", 40)), 1), 200)

    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)

    ts_language = _tree_sitter_language_name(language)
    if not ts_language:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Tree-sitter is not configured for language '{language}'.",
            },
            status=400,
        )

    try:
        parser = _get_tree_sitter_parser(ts_language)
        code_bytes = code.encode("utf-8")
        tree = parser.parse(code_bytes)
        root = tree.root_node

        
        hotspots = _analyze_ast_complexity(root, code_bytes)
        ast_data = _serialize_ast_node(

            root,
            code_bytes,
            depth=0,
            max_depth=max_depth,
            max_children=max_children,
        )

        sexp = ""
        if hasattr(root, "sexp"):
            try:
                sexp = root.sexp()
            except Exception:
                sexp = ""

        concept_signals = _collect_concept_signals(root)
        snapshot = None
        progress = None

        if not live_preview:
            profile = _get_or_create_profile(request)
            snapshot = _save_snapshot(filename, language, code, concept_signals, profile=profile)
            progress = _build_progress_payload(language=language, limit=20)
            _update_knowledge(profile, concept_signals.get("concept_tag"), concept_signals.get("confidence_score", 0))
            _grant_credits(profile, 2, "AST analysis completed")

        return JsonResponse(
            {
                "ok": True,
                "language": ts_language,
                "root_type": root.type,
                "has_error": bool(root.has_error),
                "sexp": sexp,
                "hotspots": hotspots,
                "ast": ast_data,
                "concept_signals": concept_signals,
                "snapshot_id": snapshot.id if snapshot else None,
                "progress": progress,
                "live_preview": live_preview,
            }
        )
    except Exception as error:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Tree-sitter parse failed: {error}",
            },
            status=400,
        )


def _compile_and_run_c(code, stdin_text, timeout):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / "main.c"
        binary = temp_path / "main.exe"
        source.write_text(code, encoding="utf-8")

        compile_result = _run_process(["gcc", str(source), "-o", str(binary)], timeout=timeout)
        if compile_result["exit_code"] != 0:
            return {
                "ok": False,
                "stage": "compile",
                "stdout": compile_result["stdout"],
                "stderr": compile_result["stderr"],
                "exit_code": compile_result["exit_code"],
            }

        run_result = _run_process([str(binary)], cwd=temp_path, stdin_text=stdin_text, timeout=timeout)
        return {
            "ok": True,
            "stage": "run",
            **run_result,
        }


def _compile_and_run_cpp(code, stdin_text, timeout):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / "main.cpp"
        binary = temp_path / "main.exe"
        source.write_text(code, encoding="utf-8")

        compile_result = _run_process(["g++", str(source), "-o", str(binary)], timeout=timeout)
        if compile_result["exit_code"] != 0:
            return {
                "ok": False,
                "stage": "compile",
                "stdout": compile_result["stdout"],
                "stderr": compile_result["stderr"],
                "exit_code": compile_result["exit_code"],
            }

        run_result = _run_process([str(binary)], cwd=temp_path, stdin_text=stdin_text, timeout=timeout)
        return {
            "ok": True,
            "stage": "run",
            **run_result,
        }


def _compile_and_run_java(code, stdin_text, timeout):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / "Main.java"
        source.write_text(code, encoding="utf-8")

        compile_result = _run_process(["javac", str(source)], cwd=temp_path, timeout=timeout)
        if compile_result["exit_code"] != 0:
            return {
                "ok": False,
                "stage": "compile",
                "stdout": compile_result["stdout"],
                "stderr": compile_result["stderr"],
                "exit_code": compile_result["exit_code"],
            }

        run_result = _run_process(["java", "-cp", str(temp_path), "Main"], cwd=temp_path, stdin_text=stdin_text, timeout=timeout)
        return {
            "ok": True,
            "stage": "run",
            **run_result,
        }


@csrf_exempt
def run_code_local(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    language = str(payload.get("language", "")).strip().lower()
    code = str(payload.get("code", ""))
    stdin_text = str(payload.get("stdin", ""))
    timeout = min(max(int(payload.get("timeout", 5)), 1), 15)

    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)

    try:
        if language in ("javascript", "typescript"):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "JavaScript is configured to run in browser runtime. Use the Runtime tab local run button from editor.",
                },
                status=400,
            )

        if language == "python":
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                source = temp_path / "main.py"
                source.write_text(code, encoding="utf-8")
                result = _run_process([sys.executable, str(source)], cwd=temp_path, stdin_text=stdin_text, timeout=timeout)
                return JsonResponse({"ok": True, "stage": "run", **result})

        if language == "c":
            return JsonResponse(_compile_and_run_c(code, stdin_text, timeout))

        if language == "cpp":
            return JsonResponse(_compile_and_run_cpp(code, stdin_text, timeout))

        if language == "java":
            return JsonResponse(_compile_and_run_java(code, stdin_text, timeout))

        return JsonResponse(
            {
                "ok": False,
                "error": f"Language '{language}' is not supported for local run yet.",
            },
            status=400,
        )
    except FileNotFoundError as error:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Required runtime/compiler not found: {error}",
            },
            status=400,
        )
    except subprocess.TimeoutExpired:
        return JsonResponse(
            {
                "ok": False,
                "error": "Execution timed out.",
            },
            status=408,
        )


@csrf_exempt
def format_code_local(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    language = str(payload.get("language", "")).strip().lower()
    code = str(payload.get("code", ""))
    timeout = min(max(int(payload.get("timeout", 5)), 1), 15)

    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)

    try:
        if language == "json":
            try:
                parsed = json.loads(code)
            except json.JSONDecodeError as error:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": f"Invalid JSON: {error}",
                    },
                    status=400,
                )
            formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
            return JsonResponse(_format_response("python-json", f"{formatted}\n", code))

        if language in ("javascript", "html", "css"):
            if jsbeautifier is None:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "jsbeautifier is not installed. Install dependencies from requirements.txt.",
                    },
                    status=400,
                )

            formatted = _beautify_with_jsbeautifier(language, code)
            if formatted is None:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": f"jsbeautifier formatter path unavailable for {language}.",
                    },
                    status=400,
                )

            return JsonResponse(_format_response("jsbeautifier", formatted, code))

        if language == "python":
            result = _format_with_command(
                [sys.executable, "-m", "black", "--quiet", "-"],
                code,
                timeout=timeout,
            )
            if result.returncode != 0:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (result.stderr or "Python formatter failed.").strip(),
                    },
                    status=400,
                )
            return JsonResponse(_format_response("black", result.stdout, code))

        if language == "c":
            clang_format = _find_clang_format()
            if not clang_format:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "clang-format is not installed. Install LLVM/clang-format and add it to PATH.",
                    },
                    status=400,
                )
            result = _format_with_command(
                [
                    clang_format,
                    "--style={BasedOnStyle: LLVM, BreakBeforeBraces: Allman, IndentWidth: 4, ColumnLimit: 100}",
                    "--assume-filename=main.c",
                ],
                code,
                timeout=timeout,
            )
            if result.returncode != 0:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (result.stderr or "C formatter failed.").strip(),
                    },
                    status=400,
                )
            return JsonResponse(_format_response("clang-format", result.stdout, code))

        if language == "cpp":
            clang_format = _find_clang_format()
            if not clang_format:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "clang-format is not installed. Install LLVM/clang-format and add it to PATH.",
                    },
                    status=400,
                )
            result = _format_with_command(
                [
                    clang_format,
                    "--style={BasedOnStyle: LLVM, BreakBeforeBraces: Allman, IndentWidth: 4, ColumnLimit: 100}",
                    "--assume-filename=main.cpp",
                ],
                code,
                timeout=timeout,
            )
            if result.returncode != 0:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (result.stderr or "C++ formatter failed.").strip(),
                    },
                    status=400,
                )
            return JsonResponse(_format_response("clang-format", result.stdout, code))

        if language == "java":
            google_format_command = _resolve_google_java_format_command()
            if google_format_command:
                result = _format_with_command(google_format_command, code, timeout=timeout)
                if result.returncode == 0:
                    return JsonResponse(_format_response("google-java-format", result.stdout, code))
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (result.stderr or "Java formatter failed.").strip(),
                    },
                    status=400,
                )

            clang_format = _find_clang_format()
            if clang_format:
                fallback = _format_with_command(
                    [
                        clang_format,
                        "--style={BasedOnStyle: LLVM, BreakBeforeBraces: Allman, IndentWidth: 4, ColumnLimit: 100}",
                        "--assume-filename=Main.java",
                    ],
                    code,
                    timeout=timeout,
                )
                if fallback.returncode != 0:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": (fallback.stderr or "Java formatter failed.").strip(),
                        },
                        status=400,
                    )
                return JsonResponse(_format_response("clang-format", fallback.stdout, code))

            return JsonResponse(
                {
                    "ok": False,
                    "error": "No Java formatter found. Install google-java-format or clang-format and add it to PATH.",
                },
                status=400,
            )

        return JsonResponse(
            {
                "ok": False,
                "error": f"Formatter not configured for language '{language}'.",
            },
            status=400,
        )
    except FileNotFoundError as error:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Required formatter not found: {error}",
            },
            status=400,
        )
    except UnicodeEncodeError as error:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Unicode encode issue while formatting: {error}",
            },
            status=400,
        )
    except subprocess.TimeoutExpired:
        return JsonResponse(
            {
                "ok": False,
                "error": "Formatting timed out.",
            },
            status=408,
        )


def progress_timeline_local(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    language = str(request.GET.get("language", "")).strip().lower()
    limit_param = request.GET.get("limit", "20")
    try:
        limit = min(max(int(limit_param), 1), 100)
    except ValueError:
        limit = 20

    return JsonResponse({"ok": True, **_build_progress_payload(language=language, limit=limit)})


@csrf_exempt
def learner_action_local(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    action_type = str(payload.get("action_type", "")).strip()
    if not action_type:
        return JsonResponse({"ok": False, "error": "action_type is required."}, status=400)

    snapshot_id = payload.get("snapshot_id")
    snapshot = None
    if snapshot_id:
        snapshot = DiagnosticSnapshot.objects.filter(id=snapshot_id).first()

    action = LearnerAction.objects.create(
        snapshot=snapshot,
        action_type=action_type,
        filename=str(payload.get("filename", "")).strip(),
        language=str(payload.get("language", "")).strip().lower(),
        metadata=payload.get("metadata", {}),
    )

    return JsonResponse({"ok": True, "action_id": action.id})


@csrf_exempt
def ai_diagnose_local(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    language = str(payload.get("language", "")).strip().lower()
    filename = str(payload.get("filename", "untitled")).strip() or "untitled"
    code = str(payload.get("code", ""))
    concept_signals = payload.get("concept_signals", {})

    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)

    # Check credits
    profile = _get_or_create_profile(request)
    if profile.credits < 5:
        return JsonResponse({"ok": False, "error": f"Not enough credits. You have {profile.credits}, need 5."}, status=400)

    # Calculate Pedagogical Failed Attempts for Progressive Hinting
    from datetime import timedelta
    from django.utils import timezone
    recent_time_window = timezone.now() - timedelta(hours=2)
    failed_attempts = DiagnosticSnapshot.objects.filter(
        profile=profile,
        filename=filename,
        created_at__gte=recent_time_window,
        confidence_score__lt=65
    ).count()

    _use_credits(profile, 5, "AI diagnosis")

    # Build Memory Context for the AI (Conceptual Memory Engine)
    memory_context = _build_memory_context(profile)

    ai_result = diagnose_with_inception(
        code=code,
        language=language,
        filename=filename,
        concept_signals=concept_signals if isinstance(concept_signals, dict) else {},
        failed_attempts=failed_attempts,
        memory_context=memory_context,
    )

    if not ai_result.get("ok"):
        # Refund credits on AI failure
        _grant_credits(profile, 5, "AI diagnosis refund (service error)")
        return JsonResponse(ai_result, status=400)

    diagnosis = ai_result.get("diagnosis", {})
    confidence_adjustment = int(diagnosis.get("confidence_adjustment", 0) or 0)
    confidence_adjustment = max(-30, min(30, confidence_adjustment))

    base_confidence = int(concept_signals.get("confidence_score", 60) or 60)
    ai_confidence = max(0, min(100, base_confidence + confidence_adjustment))

    if ai_confidence >= 80:
        confidence_label = "Strong"
    elif ai_confidence >= 60:
        confidence_label = "Moderate"
    elif ai_confidence >= 40:
        confidence_label = "Developing"
    else:
        confidence_label = "Needs Reinforcement"

    merged_signals = {
        **(concept_signals if isinstance(concept_signals, dict) else {}),
        "issue": diagnosis.get("issue", concept_signals.get("issue", "")),
        "concept_gap": diagnosis.get("concept_gap", concept_signals.get("concept_gap", "")),
        "suggestion": diagnosis.get("suggestion", concept_signals.get("suggestion", "")),
        "fix_now": diagnosis.get("fix_now", concept_signals.get("fix_now", "")),
        "learn_now": diagnosis.get("learn_now", concept_signals.get("learn_now", "")),
        "practice_now": diagnosis.get("practice_now", concept_signals.get("practice_now", "")),
        "confidence_score": ai_confidence,
        "confidence_label": confidence_label,
    }

    snapshot = _save_snapshot(filename, language, code, merged_signals)
    LearnerAction.objects.create(
        snapshot=snapshot,
        action_type="ai_diagnose",
        filename=filename,
        language=language,
        metadata={"source": "inception", "confidence_adjustment": confidence_adjustment},
    )

    # Update knowledge
    _update_knowledge(profile, merged_signals.get("concept_tag"), ai_confidence)
    _grant_credits(profile, 3, "AI analysis completed bonus")

    return JsonResponse(
        {
            "ok": True,
            "diagnosis": diagnosis,
            "concept_signals": merged_signals,
            "snapshot_id": snapshot.id,
            "progress": _build_progress_payload(language=language, limit=20),
            "credits_remaining": profile.credits,
        }
    )


def formatter_status_local(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    black_path = _find_executable(["black", "black.exe"])
    clang_path = _find_clang_format()
    google_java_cmd = _resolve_google_java_format_command()

    return JsonResponse(
        {
            "ok": True,
            "status": {
                "black": {"available": bool(black_path), "path": black_path or ""},
                "clang_format": {"available": bool(clang_path), "path": clang_path or ""},
                "google_java_format": {"available": bool(google_java_cmd), "command": google_java_cmd or []},
                "jsbeautifier": {"available": jsbeautifier is not None},
                "tree_sitter": {"available": _tree_sitter_backend_available()},
                "ai_key": {"available": bool(get_inception_api_key())},
            },
        }
    )


# --- New API endpoints ---

def dashboard_stats_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)
    total_analyses = DiagnosticSnapshot.objects.count()
    progress = _build_progress_payload(limit=50)

    knowledge_entries = list(
        KnowledgeEntry.objects.filter(user=profile).values(
            "concept_tag", "proficiency_level", "practice_count", "last_practiced"
        )
    )
    for entry in knowledge_entries:
        if entry.get("last_practiced"):
            entry["last_practiced"] = entry["last_practiced"].isoformat()

    recent = list(
        DiagnosticSnapshot.objects.order_by("-created_at")[:5].values(
            "id", "filename", "language", "confidence_score", "confidence_label",
            "concept_tag", "issue", "created_at"
        )
    )
    for item in recent:
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()

    return JsonResponse({
        "ok": True,
        "credits": profile.credits,
        "total_analyses": total_analyses,
        "average_confidence": progress.get("average_confidence", 0),
        "concept_counts": progress.get("concept_counts", {}),
        "knowledge": knowledge_entries,
        "recent_activity": recent,
    })


def knowledge_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)
    entries = list(
        KnowledgeEntry.objects.filter(user=profile).order_by("-last_practiced").values(
            "concept_tag", "proficiency_level", "practice_count", "first_seen", "last_practiced"
        )
    )
    for entry in entries:
        if entry.get("first_seen"):
            entry["first_seen"] = entry["first_seen"].isoformat()
        if entry.get("last_practiced"):
            entry["last_practiced"] = entry["last_practiced"].isoformat()

    return JsonResponse({"ok": True, "knowledge": entries})


def knowledge_graph_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)

    knowledge_entries = list(
        KnowledgeEntry.objects.filter(user=profile).order_by("-practice_count", "-last_practiced")[:30]
    )
    snapshots = list(
        DiagnosticSnapshot.objects.filter(profile=profile).order_by("-created_at")[:80].values(
            "concept_tag",
            "filename",
            "language",
            "confidence_score",
        )
    )

    nodes = [
        {
            "id": "learner",
            "label": profile.display_name or "Learner",
            "type": "user",
            "size": 18,
            "group": "user",
            "meta": {
                "credits": profile.credits,
                "submissions": len(snapshots),
            },
        }
    ]
    links = []

    concept_map = {}
    weak_concepts = []

    for entry in knowledge_entries:
        concept_id = f"concept::{entry.concept_tag}"
        concept_map[entry.concept_tag] = concept_id

        if entry.proficiency_level in ("beginner", "developing"):
            weak_concepts.append(entry.concept_tag)

        nodes.append(
            {
                "id": concept_id,
                "label": entry.concept_tag,
                "type": "concept",
                "group": entry.proficiency_level,
                "size": max(8, min(20, 8 + entry.practice_count)),
                "meta": {
                    "proficiency": entry.proficiency_level,
                    "practice_count": entry.practice_count,
                },
            }
        )

        links.append(
            {
                "source": "learner",
                "target": concept_id,
                "kind": "learned",
                "weight": max(1, entry.practice_count),
            }
        )

    from collections import Counter

    co_occurrence = Counter()
    seen_tags = []
    for shot in snapshots:
        tag = (shot.get("concept_tag") or "").strip()
        if not tag:
            continue
        seen_tags.append(tag)

    for i in range(len(seen_tags) - 1):
        a = seen_tags[i]
        b = seen_tags[i + 1]
        if a and b and a != b:
            key = tuple(sorted((a, b)))
            co_occurrence[key] += 1

    for (left_tag, right_tag), count in co_occurrence.items():
        left_id = concept_map.get(left_tag)
        right_id = concept_map.get(right_tag)
        if left_id and right_id:
            links.append(
                {
                    "source": left_id,
                    "target": right_id,
                    "kind": "related",
                    "weight": min(6, count),
                }
            )

    return JsonResponse(
        {
            "ok": True,
            "graph": {
                "nodes": nodes,
                "links": links,
            },
            "summary": {
                "concept_count": len(concept_map),
                "weak_concepts": weak_concepts[:5],
                "resource_count": 0,
            },
        }
    )


def credits_balance_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)
    transactions = list(
        CreditTransaction.objects.filter(user=profile).order_by("-created_at")[:20].values(
            "amount", "reason", "created_at"
        )
    )
    for txn in transactions:
        if txn.get("created_at"):
            txn["created_at"] = txn["created_at"].isoformat()

    return JsonResponse({
        "ok": True,
        "credits": profile.credits,
        "transactions": transactions,
    })


def youtube_recommend_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)
    weak_concepts = list(
        KnowledgeEntry.objects.filter(
            user=profile,
            proficiency_level__in=["beginner", "developing"]
        ).order_by("proficiency_level", "-practice_count").values_list("concept_tag", flat=True)[:5]
    )

    if not weak_concepts:
        weak_concepts = ["general-structure"]
        
    # NEW logic: Generate AI Queries directly
    ai_resp = generate_youtube_queries(concepts=weak_concepts)
    queries = ai_resp.get("queries", []) if ai_resp.get("ok") else [f"{c} programming tutorial" for c in weak_concepts][:5]
    if not queries:
        queries = [f"{c} programming tutorial" for c in weak_concepts][:5]
        
    recommendations = []
    
    api_key = _read_youtube_api_key()
    if api_key:
        import requests as http_requests
        # We have an API key, let's fetch the top video for each query
        for q in queries:
            try:
                params = {
                    "part": "snippet",
                    "q": q,
                    "type": "video",
                    "maxResults": 1,
                    "videoCategoryId": "27",
                    "key": api_key,
                }
                resp = http_requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=5)
                data = resp.json()
                if resp.status_code < 400 and data.get("items"):
                    item = data["items"][0]
                    snippet = item.get("snippet", {})
                    video_id = item.get("id", {}).get("videoId", "")
                    recommendations.append({
                        "title": snippet.get("title", ""),
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "channel": snippet.get("channelTitle", ""),
                        "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                        "concept": q,
                        "source_concept": q,
                    })
            except Exception:
                pass
    
    # Fallback if no API key or API limits
    if not recommendations:
        for q in queries:
            recommendations.append({
                "title": f"Suggested Search: {q}",
                "url": f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}",
                "channel": "YouTube Search",
                "thumbnail": "",
                "concept": q,
                "source_concept": q,
            })

    return JsonResponse({"ok": True, "recommendations": recommendations, "weak_concepts": weak_concepts})


@csrf_exempt
def video_watched_api(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    profile = _get_or_create_profile(request)

    title = str(payload.get("title", "")).strip()
    url = str(payload.get("url", "")).strip()
    concept = str(payload.get("concept", "")).strip()
    source_concept = str(payload.get("source_concept", "")).strip()

    if not title:
        return JsonResponse({"ok": False, "error": "Video title is required."}, status=400)

    concept_tag = (source_concept or concept or "general-structure")[:120]

    entry, created = KnowledgeEntry.objects.get_or_create(
        user=profile,
        concept_tag=concept_tag,
        defaults={"proficiency_level": "developing", "practice_count": 1},
    )

    if not created:
        entry.practice_count += 1
        if entry.proficiency_level == "beginner":
            entry.proficiency_level = "developing"
        elif entry.proficiency_level == "developing" and entry.practice_count >= 8:
            entry.proficiency_level = "strong"
        entry.last_practiced = timezone.now()
        entry.save()

    LearnerAction.objects.create(
        action_type="video_watched",
        filename="",
        language="",
        metadata={
            "title": title,
            "url": url,
            "concept": concept,
            "source_concept": concept_tag,
        },
    )

    _grant_credits(profile, 1, "Watched learning resource")

    return JsonResponse(
        {
            "ok": True,
            "knowledge": {
                "concept_tag": entry.concept_tag,
                "practice_count": entry.practice_count,
                "proficiency_level": entry.proficiency_level,
            },
            "credits_remaining": profile.credits,
        }
    )

@csrf_exempt
def ast_flowchart_api(request):
    """Generate a Mermaid flowchart from code using AI."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    code = str(payload.get("code", ""))
    language = str(payload.get("language", "")).strip().lower()
    concept_signals = payload.get("concept_signals", {})
    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)
    profile = _get_or_create_profile(request)
    if profile.credits < 3:
        return JsonResponse(
            {"ok": False, "error": f"Not enough credits. You have {profile.credits}, need 3."},
            status=400,
        )
    _use_credits(profile, 3, "Flowchart generation")
    result = generate_mermaid_flowchart(
        code=code,
        language=language,
        concept_signals=concept_signals if isinstance(concept_signals, dict) else {},
    )
    if not result.get("ok"):
        _grant_credits(profile, 3, "Flowchart generation refund (AI error)")
        return JsonResponse(result, status=400)
    return JsonResponse({
        "ok": True,
        "mermaid_code": result["mermaid_code"],
        "credits_remaining": profile.credits,
    })


def _read_youtube_api_key():
    """Read YOUTUBE_API_KEY from env or .env file."""
    env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env_key:
        return env_key
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if not dotenv_path.exists():
        return ""
    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("YOUTUBE_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
                    value = value[1:-1]
                return value.strip()
    except OSError:
        pass
    return ""


def youtube_search_api(request):
    """Search YouTube for educational videos. Falls back to hardcoded if no API key."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"ok": False, "error": "Search query is required."}, status=400)
    api_key = _read_youtube_api_key()
    if not api_key:
        results = []
        for concept, videos in YOUTUBE_RECOMMENDATIONS.items():
            for video in videos:
                if query.lower() in video["title"].lower() or query.lower() in concept:
                    results.append({**video, "concept": concept})
        if not results:
            for concept, videos in YOUTUBE_RECOMMENDATIONS.items():
                for video in videos:
                    results.append({**video, "concept": concept})
        return JsonResponse({"ok": True, "results": results[:9], "source": "hardcoded"})
    import requests as http_requests
    try:
        params = {
            "part": "snippet",
            "q": f"{query} programming tutorial",
            "type": "video",
            "maxResults": 6,
            "videoCategoryId": "27",
            "key": api_key,
        }
        resp = http_requests.get(
            "https://www.googleapis.com/youtube/v3/search", params=params, timeout=10
        )
        data = resp.json()
        if resp.status_code >= 400:
            return JsonResponse(
                {"ok": False, "error": data.get("error", {}).get("message", "YouTube API error")},
                status=400,
            )
        results = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            results.append({
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "channel": snippet.get("channelTitle", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "description": snippet.get("description", "")[:150],
            })
        return JsonResponse({"ok": True, "results": results, "source": "youtube_api"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"YouTube search failed: {e}"}, status=500)


@csrf_exempt
def notes_api(request):
    """CRUD for user notes."""
    profile = _get_or_create_profile(request)
    if request.method == "GET":
        notes = Note.objects.filter(user=profile).values(
            "id", "title", "content", "tags", "filename", "created_at", "updated_at"
        )[:50]
        return JsonResponse({"ok": True, "notes": list(notes)})
    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        note_id = payload.get("id")
        title = str(payload.get("title", "Untitled Note")).strip()[:200]
        content = str(payload.get("content", ""))
        tags = str(payload.get("tags", "")).strip()[:300]
        filename = str(payload.get("filename", "")).strip()[:255]
        snapshot_id = payload.get("snapshot_id")
        snapshot = None
        if snapshot_id:
            try:
                snapshot = DiagnosticSnapshot.objects.get(id=snapshot_id)
            except DiagnosticSnapshot.DoesNotExist:
                pass
        if note_id:
            try:
                note = Note.objects.get(id=note_id, user=profile)
                note.title = title
                note.content = content
                note.tags = tags
                note.filename = filename
                if snapshot:
                    note.snapshot = snapshot
                note.save()
            except Note.DoesNotExist:
                return JsonResponse({"ok": False, "error": "Note not found."}, status=404)
        else:
            note = Note.objects.create(
                user=profile, title=title, content=content,
                tags=tags, filename=filename, snapshot=snapshot,
            )
        return JsonResponse({
            "ok": True,
            "note": {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "filename": note.filename,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat(),
            },
        })
    if request.method == "DELETE":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        note_id = payload.get("id")
        if not note_id:
            return JsonResponse({"ok": False, "error": "Note ID required."}, status=400)
        deleted, _ = Note.objects.filter(id=note_id, user=profile).delete()
        if deleted == 0:
            return JsonResponse({"ok": False, "error": "Note not found."}, status=404)
        return JsonResponse({"ok": True, "deleted": note_id})
    return HttpResponseNotAllowed(["GET", "POST", "DELETE"])


@csrf_exempt
def ast_flowchart_api(request):
    """Generate a Mermaid flowchart from code using AI."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    code = str(payload.get("code", ""))
    language = str(payload.get("language", "")).strip().lower()
    concept_signals = payload.get("concept_signals", {})
    if not code.strip():
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)
    profile = _get_or_create_profile(request)
    if profile.credits < 3:
        return JsonResponse(
            {"ok": False, "error": f"Not enough credits. You have {profile.credits}, need 3."},
            status=400,
        )
    _use_credits(profile, 3, "Flowchart generation")
    result = generate_mermaid_flowchart(
        code=code,
        language=language,
        concept_signals=concept_signals if isinstance(concept_signals, dict) else {},
    )
    if not result.get("ok"):
        _grant_credits(profile, 3, "Flowchart generation refund (AI error)")
        return JsonResponse(result, status=400)
    return JsonResponse({
        "ok": True,
        "mermaid_code": result["mermaid_code"],
        "credits_remaining": profile.credits,
    })


def _read_youtube_api_key():
    """Read YOUTUBE_API_KEY from env or .env file."""
    env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env_key:
        return env_key
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if not dotenv_path.exists():
        return ""
    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("YOUTUBE_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
                    value = value[1:-1]
                return value.strip()
    except OSError:
        pass
    return ""


def youtube_search_api(request):
    """Search YouTube for educational videos. Falls back to hardcoded if no API key."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"ok": False, "error": "Search query is required."}, status=400)
    api_key = _read_youtube_api_key()
    if not api_key:
        results = []
        for concept, videos in YOUTUBE_RECOMMENDATIONS.items():
            for video in videos:
                if query.lower() in video["title"].lower() or query.lower() in concept:
                    results.append({**video, "concept": concept})
        if not results:
            for concept, videos in YOUTUBE_RECOMMENDATIONS.items():
                for video in videos:
                    results.append({**video, "concept": concept})
        return JsonResponse({"ok": True, "results": results[:9], "source": "hardcoded"})
    import requests as http_requests
    try:
        params = {
            "part": "snippet",
            "q": f"{query} programming tutorial",
            "type": "video",
            "maxResults": 6,
            "videoCategoryId": "27",
            "key": api_key,
        }
        resp = http_requests.get(
            "https://www.googleapis.com/youtube/v3/search", params=params, timeout=10
        )
        data = resp.json()
        if resp.status_code >= 400:
            return JsonResponse(
                {"ok": False, "error": data.get("error", {}).get("message", "YouTube API error")},
                status=400,
            )
        results = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            results.append({
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "channel": snippet.get("channelTitle", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "description": snippet.get("description", "")[:150],
            })
        return JsonResponse({"ok": True, "results": results, "source": "youtube_api"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"YouTube search failed: {e}"}, status=500)


@csrf_exempt
def notes_api(request):
    """CRUD for user notes."""
    profile = _get_or_create_profile(request)
    if request.method == "GET":
        notes = Note.objects.filter(user=profile).values(
            "id", "title", "content", "tags", "filename", "created_at", "updated_at"
        )[:50]
        return JsonResponse({"ok": True, "notes": list(notes)})
    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        note_id = payload.get("id")
        title = str(payload.get("title", "Untitled Note")).strip()[:200]
        content = str(payload.get("content", ""))
        tags = str(payload.get("tags", "")).strip()[:300]
        filename = str(payload.get("filename", "")).strip()[:255]
        snapshot_id = payload.get("snapshot_id")
        snapshot = None
        if snapshot_id:
            try:
                snapshot = DiagnosticSnapshot.objects.get(id=snapshot_id)
            except DiagnosticSnapshot.DoesNotExist:
                pass
        if note_id:
            try:
                note = Note.objects.get(id=note_id, user=profile)
                note.title = title
                note.content = content
                note.tags = tags
                note.filename = filename
                if snapshot:
                    note.snapshot = snapshot
                note.save()
            except Note.DoesNotExist:
                return JsonResponse({"ok": False, "error": "Note not found."}, status=404)
        else:
            note = Note.objects.create(
                user=profile, title=title, content=content,
                tags=tags, filename=filename, snapshot=snapshot,
            )
        return JsonResponse({
            "ok": True,
            "note": {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "filename": note.filename,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat(),
            },
        })
    if request.method == "DELETE":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        note_id = payload.get("id")
        if not note_id:
            return JsonResponse({"ok": False, "error": "Note ID required."}, status=400)
        deleted, _ = Note.objects.filter(id=note_id, user=profile).delete()
        if deleted == 0:
            return JsonResponse({"ok": False, "error": "Note not found."}, status=404)
        return JsonResponse({"ok": True, "deleted": note_id})
    return HttpResponseNotAllowed(["GET", "POST", "DELETE"])


def _build_workspace_duck_context(language: str = "", max_files: int = 80, max_snippets: int = 6) -> dict:
    root = Path(__file__).resolve().parent.parent
    allowed_suffixes = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp",
        ".go", ".php", ".rb", ".html", ".css", ".json", ".md",
    }
    skip_parts = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}

    files = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        if any(part in skip_parts for part in path.parts):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        files.append(rel)

    language_hint = str(language or "").lower()
    preferred = []
    for rel in files:
        if language_hint in ("python", "py") and rel.endswith(".py"):
            preferred.append(rel)
        elif language_hint in ("javascript", "typescript", "js", "ts") and (rel.endswith(".js") or rel.endswith(".ts")):
            preferred.append(rel)
        elif language_hint in ("html", "css", "json") and rel.endswith(f".{language_hint}"):
            preferred.append(rel)

    snippet_targets = preferred[:max_snippets] if preferred else files[:max_snippets]
    snippets = []
    for rel in snippet_targets:
        abs_path = root / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            preview_lines = [line for line in text.splitlines()[:14] if line.strip()]
            preview = "\n".join(preview_lines)[:850]
            snippets.append({"path": rel, "preview": preview})
        except OSError:
            continue

    return {
        "workspace_root": root.name,
        "file_count": len(files),
        "files": files,
        "snippets": snippets,
    }


@csrf_exempt
def rubber_duck_api(request):
    """
    Rubber Duck Mode endpoint.
    Accepts code, conversation history, and the latest student message.
    Returns a Socratic probing question from the AI.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    code = str(payload.get("code", ""))
    filename = str(payload.get("filename", "untitled")).strip() or "untitled"
    language = str(payload.get("language", "python")).strip().lower()
    user_message = str(payload.get("message", "")).strip()
    history = payload.get("history", [])
    editor_context_payload = payload.get("editor_context", {})
    concept_signals_payload = payload.get("concept_signals", {})

    if not code.strip():
        return JsonResponse({"ok": False, "error": "No code provided."}, status=400)
    if not user_message:
        return JsonResponse({"ok": False, "error": "No message provided."}, status=400)
    if not isinstance(history, list):
        history = []
    if not isinstance(editor_context_payload, dict):
        editor_context_payload = {}
    if not isinstance(concept_signals_payload, dict):
        concept_signals_payload = {}

    profile = _get_or_create_profile(request)
    memory_context = _build_memory_context(profile)
    failed_attempts = max(0, len(history) // 2)

    code_context = {
        "language": language,
        "concept_signals": {},
        "hotspots": [],
        "parse_error": False,
        "filename": filename,
        "code_excerpt": code[:2200],
        "symbol_hints": [],
        "editor_focus": {
            "cursor_line": editor_context_payload.get("cursor_line"),
            "cursor_column": editor_context_payload.get("cursor_column"),
            "selection_start_line": editor_context_payload.get("selection_start_line"),
            "selection_end_line": editor_context_payload.get("selection_end_line"),
            "selected_text": str(editor_context_payload.get("selected_text", ""))[:260],
        },
    }

    code_context["workspace_context"] = _build_workspace_duck_context(language=language)

    if concept_signals_payload:
        code_context["concept_signals"] = concept_signals_payload

    try:
        code_lines = code.splitlines()
        cursor_line_raw = code_context["editor_focus"].get("cursor_line")
        selection_start_raw = code_context["editor_focus"].get("selection_start_line")
        selection_end_raw = code_context["editor_focus"].get("selection_end_line")

        def _safe_line_num(value):
            try:
                line_num = int(value)
                if line_num < 1:
                    return None
                return line_num
            except (TypeError, ValueError):
                return None

        cursor_line = _safe_line_num(cursor_line_raw)
        selection_start = _safe_line_num(selection_start_raw)
        selection_end = _safe_line_num(selection_end_raw)

        line_count = len(code_lines)
        code_context["editor_focus"]["line_count"] = line_count
        code_context["editor_focus"]["cursor_line_requested"] = cursor_line

        if selection_start and selection_end and selection_end < selection_start:
            selection_start, selection_end = selection_end, selection_start

        if cursor_line and line_count > 0 and cursor_line > line_count:
            cursor_line = line_count

        focus_line = selection_start or cursor_line

        if focus_line and line_count > 0:
            focus_line = max(1, min(focus_line, line_count))

            def _nearest_non_empty_line(target_line: int) -> int:
                if not code_lines:
                    return target_line
                if code_lines[target_line - 1].strip():
                    return target_line
                for radius in range(1, line_count + 1):
                    up = target_line - radius
                    down = target_line + radius
                    if up >= 1 and code_lines[up - 1].strip():
                        return up
                    if down <= line_count and code_lines[down - 1].strip():
                        return down
                return target_line

            focus_line = _nearest_non_empty_line(focus_line)
            code_context["editor_focus_line"] = focus_line
            code_context["editor_focus_line_text"] = code_lines[focus_line - 1][:220]

            start = max(1, focus_line - 1)
            end = min(line_count, focus_line + 1)
            neighborhood = []
            for line_no in range(start, end + 1):
                neighborhood.append(f"L{line_no}: {code_lines[line_no - 1][:180]}")
            code_context["editor_focus_neighborhood"] = neighborhood

        if selection_start and selection_end and selection_start <= len(code_lines):
            selection_end = min(selection_end or selection_start, len(code_lines))
            selected_block = "\n".join(code_lines[selection_start - 1:selection_end])
            if selected_block:
                code_context["editor_selected_block"] = selected_block[:700]
    except Exception:
        pass

    try:
        import re
        symbols = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", code)
        filtered = [
            token for token in symbols
            if token not in {"for", "while", "if", "else", "return", "class", "def", "function", "var", "let", "const"}
        ]
        dedup = []
        for token in filtered:
            if token not in dedup:
                dedup.append(token)
            if len(dedup) >= 10:
                break
        code_context["symbol_hints"] = dedup
    except Exception:
        pass

    ts_language = _tree_sitter_language_name(language)
    if ts_language and _tree_sitter_backend_available():
        try:
            parser = _get_tree_sitter_parser(ts_language)
            code_bytes = code.encode("utf-8")
            tree = parser.parse(code_bytes)
            root = tree.root_node
            code_context["parse_error"] = bool(root.has_error)
            ast_signals = _collect_concept_signals(root)
            code_context["concept_signals"] = {**ast_signals, **(code_context.get("concept_signals") or {})}
            code_context["hotspots"] = _analyze_ast_complexity(root, code_bytes)[:5]
        except Exception:
            pass

    from .ai_client import rubber_duck_chat
    result = rubber_duck_chat(
        code=code,
        language=language,
        history=history,
        user_message=user_message,
        memory_context=memory_context,
        code_context=code_context,
        failed_attempts=failed_attempts,
    )

    if not result.get("ok"):
        return JsonResponse(result, status=400)

    LearnerAction.objects.create(
        action_type="rubber_duck_turn",
        filename="",
        language=language,
        metadata={
            "history_turns": len(history),
            "message_len": len(user_message),
            "used_memory_context": bool(memory_context),
            "has_ast_context": bool(code_context.get("concept_signals")),
        },
    )

    return JsonResponse({"ok": True, "reply": result["reply"]})


def _build_memory_context(profile) -> str:
    """
    Conceptual Memory Engine: summarise the student's recent learning history
    into a compact text paragraph the AI can reference during diagnosis.
    """
    try:
        snapshots = (
            DiagnosticSnapshot.objects
            .filter(profile=profile)
            .order_by("-created_at")[:20]
            .values("concept_tag", "confidence_score", "filename", "created_at")
        )
        if not snapshots:
            return ""

        from collections import Counter
        concept_count = Counter()
        low_conf_concepts = Counter()  # concepts with confidence < 60
        files_seen = set()

        for s in snapshots:
            tag = s.get("concept_tag") or "General"
            conf = s.get("confidence_score") or 60
            concept_count[tag] += 1
            if conf < 60:
                low_conf_concepts[tag] += 1
            files_seen.add(s.get("filename", "unknown"))

        lines = []
        total = sum(concept_count.values())
        lines.append(f"Student has made {total} code submissions across {len(files_seen)} file(s).")

        if concept_count:
            top = concept_count.most_common(3)
            top_str = ", ".join(f"{tag} ({cnt}x)" for tag, cnt in top)
            lines.append(f"Most frequently analysed concepts: {top_str}.")

        if low_conf_concepts:
            weak = low_conf_concepts.most_common(3)
            weak_str = ", ".join(f"{tag} ({cnt}x)" for tag, cnt in weak)
            lines.append(f"RECURRING WEAK AREAS (confidence < 60%): {weak_str}. *** Prioritise these in diagnosis. ***")

        return " ".join(lines)
    except Exception:
        return ""


def _detect_inefficiency_patterns(code, language):
    patterns = []
    concept_signals = {}

    ts_language = _tree_sitter_language_name(language)
    if ts_language and _tree_sitter_backend_available() and code.strip():
        try:
            parser = _get_tree_sitter_parser(ts_language)
            tree = parser.parse(code.encode("utf-8"))
            concept_signals = _collect_concept_signals(tree.root_node)
        except Exception:
            concept_signals = {}

    source = code.lower()
    max_loop_nesting = int(concept_signals.get("max_loop_nesting", 0) or 0)
    conditional_count = int(concept_signals.get("conditional_count", 0) or 0)
    function_like_count = int(concept_signals.get("function_like_count", 0) or 0)
    max_depth = int(concept_signals.get("max_tree_depth", 0) or 0)

    if max_loop_nesting >= 2:
        patterns.append("nested_loops")
    if conditional_count >= 6:
        patterns.append("heavy_branching")
    if max_depth >= 18:
        patterns.append("deep_structure")
    if function_like_count == 0 and len(code) > 160:
        patterns.append("missing_function_decomposition")
    if "append(" in source and " for " in source and " in " in source:
        patterns.append("manual_accumulation_pattern")
    if "== true" in source or "== false" in source:
        patterns.append("boolean_comparison_smell")

    if not patterns:
        patterns.append("no_major_structural_inefficiency_detected")

    return {
        "patterns": patterns,
        "concept_signals": concept_signals,
    }


@csrf_exempt
def live_morph_api(request):
    """
    Live Code Morphing Optimizer endpoint.
    Returns an AI-optimized version of the code + a precise changelog.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    code = str(payload.get("code", "")).strip()
    language = str(payload.get("language", "python")).strip().lower()
    concept_signals = payload.get("concept_signals", {})

    if not code:
        return JsonResponse({"ok": False, "error": "Code is empty."}, status=400)
    if len(code) > 8000:
        return JsonResponse({"ok": False, "error": "Code too long for morphing (max 8000 chars)."}, status=400)

    profile = _get_or_create_profile(request)
    if profile.credits < 4:
        return JsonResponse(
            {"ok": False, "error": f"Not enough credits. You have {profile.credits}, need 4."},
            status=400,
        )

    _use_credits(profile, 4, "Code Morphing")

    detected = _detect_inefficiency_patterns(code, language)
    memory_context = _build_memory_context(profile)
    merged_concept_signals = {}
    if isinstance(concept_signals, dict):
        merged_concept_signals.update(concept_signals)
    if isinstance(detected.get("concept_signals"), dict):
        merged_concept_signals.update(detected.get("concept_signals", {}))

    from .ai_client import morph_code
    result = morph_code(
        code=code,
        language=language,
        concept_signals=merged_concept_signals,
        inefficiency_patterns=detected.get("patterns", []),
        memory_context=memory_context,
    )

    if not result.get("ok"):
        _grant_credits(profile, 4, "Code Morphing refund (AI error)")
        return JsonResponse(result, status=400)

    LearnerAction.objects.create(
        action_type="live_morph",
        filename="",
        language=language,
        metadata={
            "patterns": detected.get("patterns", []),
            "used_memory_context": bool(memory_context),
        },
    )

    return JsonResponse({
        "ok": True,
        "morphed_code": result["morphed_code"],
        "changes": result["changes"],
        "complexity_before": result["complexity_before"],
        "complexity_after": result["complexity_after"],
        "detected_patterns": detected.get("patterns", []),
        "concept_signals": merged_concept_signals,
        "credits_remaining": profile.credits,
    })


def memory_context_api(request):
    """
    Conceptual Memory Engine: returns the student's learning history summary
    and recent weak concepts for display in the UI.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    profile = _get_or_create_profile(request)

    try:
        from collections import Counter
        snapshots = list(
            DiagnosticSnapshot.objects
            .filter(profile=profile)
            .order_by("-created_at")[:30]
            .values("concept_tag", "confidence_score", "filename", "created_at", "language")
        )

        concept_count = Counter()
        low_conf = Counter()
        trend = []

        for s in snapshots:
            tag = s.get("concept_tag") or "General"
            conf = s.get("confidence_score") or 60
            concept_count[tag] += 1
            if conf < 60:
                low_conf[tag] += 1
            trend.append({
                "tag": tag,
                "confidence": conf,
                "filename": s.get("filename", "unknown"),
                "language": s.get("language", "?"),
                "at": s["created_at"].isoformat() if s.get("created_at") else "",
            })

        return JsonResponse({
            "ok": True,
            "total_submissions": len(snapshots),
            "top_concepts": concept_count.most_common(5),
            "weak_concepts": low_conf.most_common(5),
            "recent_trend": trend[:10],
            "memory_summary": _build_memory_context(profile),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
