# LogicLens — AI-Driven Coding Learning Diagnostics

LogicLens is a Django-based coding mentor platform that analyzes student code to identify conceptual misunderstandings, logical reasoning gaps, and recurring learning patterns.

It combines:
- static/structural analysis
- AST-based reasoning (Tree-sitter)
- adaptive progress tracking
- AI-powered pedagogical guidance

## What is implemented

### 1) Smart Monaco Editor Workspace
- Monaco code editor with upload + language detection
- filename-extension sync on language changes
- in-browser theme switching
- runtime tabs for diagnostics and outputs

### 2) Local Code Execution
- JavaScript runs in browser worker runtime
- Python, C, C++, Java run via backend subprocess pipeline
- stdin support and stdout/stderr capture

### 3) Formatting Pipeline
- Backend-first formatting strategy
- Python: `black`
- C/C++: `clang-format`
- Java: `google-java-format` (if available) with fallback
- HTML/CSS/JS: `jsbeautifier`
- JSON pretty formatting
- Formatted tab with before/after preview + apply to editor

### 4) AST + Concept Signal Engine
- Tree-sitter parse endpoint
- AST serialization for interactive tree rendering
- concept signals (loop nesting, branching load, depth)
- confidence scoring and label generation

### 5) AI Learning Diagnosis
- AI endpoint integrated via Inception API
- preferred flow implemented: **format -> AST -> AI**
- AI uses AST concept signals + code context
- returns:
  - issue
  - concept gap
  - suggestion
  - fix now
  - learn now
  - practice now

### 6) Adaptive Learning Persistence
- Diagnostic snapshots stored in DB
- learner actions logged (format/apply/parse/AI/etc.)
- progress timeline + concept frequency + average confidence

### 7) Capability / Health Transparency
- backend tool-status endpoint
- UI Problems tab displays formatter/parser/AI-key availability

---

## Current workflow (seamless)

Click **Run Analysis** in editor:
1. Format code
2. Parse AST (Tree-sitter)
3. Run AI diagnosis using AST concept signals
4. Update quick learning panel + progress timeline

If formatter/AI is unavailable, the system degrades gracefully and still provides the best possible analysis from available components.

---

## Project structure (high level)

- `codify/` Django project settings
- `logiclens/templates/editor.html` main workspace UI
- `logiclens/views.py` API endpoints (run/format/ast/ai/progress/status)
- `logiclens/urls.py` app routes
- `logiclens/models.py` adaptive learning models
- `logiclens/ai_client.py` AI client + key handling

---

## API endpoints

- `GET /` editor page
- `GET /analysis/` analysis page
- `POST /api/run-code/`
- `POST /api/format-code/`
- `POST /api/ast-tree/`
- `POST /api/ai-diagnose/`
- `GET /api/progress/`
- `POST /api/action/`
- `GET /api/formatter-status/`

---

## Setup

### 1) Install dependencies

```bash
pip install -r requirements.txt
```

### 2) Configure environment

Copy `.env.example` to `.env` and update values:

```bash
copy .env.example .env
```

Then set your API key:

```env
INCEPTION_API_KEY=your_key_here
```

### 3) Run migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 4) Start server

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

---

## Tooling notes

- `google-java-format` is optional; if unavailable, Java formatting falls back.
- Local C/C++ run requires compiler toolchain installed and available in PATH.
- If AI status shows unavailable, verify `INCEPTION_API_KEY` in environment or `.env`.

---

## Validation status

- Django system check passes (`python manage.py check`)
- Major editor pipeline implemented end-to-end
- Remaining work is optional UX polish (charts/diff enhancements/auth scoping)

---

## Security note

If an API key has ever been shared in plain text, rotate it immediately and use a new key.
