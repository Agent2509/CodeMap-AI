# CodeMap AI — Final Assignment Presentation

Use this as your speaker notes / slide source. Paste the headings and bullets into your PPT/Google Slides.

---

## 1. Problem Statement

- Developers spend a lot of time onboarding to unfamiliar codebases.
- Manual exploration is slow: open file after file, trace imports, read docs.
- Existing tools (IDEs, grep) are great for one file but poor at *big-picture* architecture.
- Need a tool that: parses a whole codebase, builds a map, and lets you ask questions in plain English.

---

## 2. Proposed Solution

**CodeMap AI**
- Upload any local codebase.
- Automatically parse it into an AST, build a dependency graph, and index it for semantic search.
- Explore the result in an interactive force-directed graph.
- Chat with an AI agent that reasons over the code, calls tools, and cites specific files and lines.

---

## 3. How the AI Agent Works (ReAct loop)

1. **Reasoning** — Agent reads the user question and decides what it needs.
2. **Thought** — It plans the next step (e.g., "I should search for authentication code").
3. **Action** — It calls one of seven tools: `search_codebase`, `get_file_content`, `find_definition`, `find_references`, `get_dependency_path`, `impact_analysis`, or `generate_diagram`.
4. **Observation** — It receives the tool result.
5. **Answer** — It synthesizes a final, cited response.

The UI shows every step in a "Reasoning" panel under each agent message.

---

## 4. Architecture / Workflow

```
User upload / local path
    │
    ▼
FastAPI Backend
├── tree-sitter parser ──► code entities (classes, functions, imports)
├── graph builder ───────► file deps + call graph ──► JSON for D3.js
├── chunker ─────────────► semantic chunks with line numbers
├── ChromaDB + Jina ─────► vector index (hybrid with BM25)
└── ReAct agent ─────────► OpenAI-compatible LLM

Frontend (Next.js + Tailwind + D3.js)
├── Sidebar: project list + file tree
├── Center: interactive force graph + code viewer
└── Right panel: chat + codebase overview
```

---

## 5. Technologies Used

| Layer | Tech |
|---|---|
| Frontend | Next.js 16, React 19, Tailwind CSS v4, D3.js |
| Backend | FastAPI, Python 3.12 |
| Parsing | tree-sitter (Python, JS/TS, Java, Go, Rust, C/C++) |
| Graph | NetworkX (deps, call graph, cycles, centrality), D3 force layout |
| Vector DB | ChromaDB (embedded, persistent) |
| Retrieval | Hybrid: Jina embeddings + BM25, fused with reciprocal rank fusion |
| Embeddings | jinaai/jina-embeddings-v2-base-code |
| LLM | OpenAI-compatible endpoint (LM Studio / DeepSeek / OpenAI) |
| Streaming | Server-Sent Events (SSE) |

---

## 6. Tools / APIs Used

- **Custom agent tools**
  - `search_codebase(query)` — hybrid (semantic + keyword) search over the indexed project.
  - `get_file_content(filepath)` — read a specific file with line numbers.
  - `find_definition(symbol)` — locate where a function/class/file is defined.
  - `find_references(symbol)` — find what imports or calls a symbol.
  - `get_dependency_path(a, b)` — shortest dependency path between two files.
  - `impact_analysis(node)` — everything affected by a change, directly and transitively.
  - `generate_diagram(components)` — produce a Mermaid.js architecture diagram.
- **FastAPI routers**
  - `/api/upload` — index a local path.
  - `/api/clone` — clone a git/GitHub URL and index it.
  - `/api/graph` and `/api/graph/insights` — nodes/links plus cycles and hub files.
  - `/api/chat/stream` — ReAct agent chat stream.
  - `/api/overview` — LLM-generated architecture summary.
  - `/api/export/markdown/{id}` — export architecture as Markdown.
  - `/api/health` — verify LLM model is reachable.

---

## 7. Demo Flow

1. Open http://localhost:3000.
2. Click **Load Sample** (FastAPI todo app, already indexed).
3. Show the interactive graph: files, classes, functions, imports.
4. Click a node on the graph to read its code beside the graph.
5. Ask the chat: *"What does this codebase do?"*
6. **The headline:** ask *"what breaks if I change crud.py?"* — the agent calls `impact_analysis` and the graph lights up the blast radius.
7. Trace a path between two nodes; the Connections panel shows each hop with the real linking line, and Explain narrates how they connect.
8. Show the **Export** button: download ARCHITECTURE.md.
9. Optional, if time allows: paste a GitHub URL (e.g. `https://github.com/pallets/itsdangerous`) to clone and map a real repository live.

**Presenter tip:** Make sure the model is loaded in LM Studio (or use a cloud key). Verify with `GET /api/health`.

---

## 8. Challenges & Learnings

- **Local models emit tool calls as raw text**, not always as native function calls. We added a streaming filter that suppresses raw tool JSON before it reaches the UI.
- **Chunk boundaries can split a tool-call marker**, causing partial leaks. We hold back 12 trailing characters so the marker is never split across SSE frames.
- **Missing `AI_MODEL` caused silent failures**. We added `.env` loading + a `/api/health` endpoint so the demo fails fast with a clear message.
- **Graph updates should not explode the physics layout**. We preserve node positions when refreshing data.
- Learned that ReAct is powerful but the model must be reliable; local 7B models are slower and loop more than hosted models.

---

## 9. Future Improvements

- **Hosted model fallback**: automatically switch providers when the primary endpoint is rate-limited.
- **Better agent memory**: keep a project-level summary across chat sessions.
- **Incremental re-indexing**: re-parse only files that changed.
- **Review mode**: point the agent at a git diff and get a code review.
- **More export formats**: PNG/SVG graph, PDF report.

---

## Quick Demo Script (for rehearsal)

```bash
# Terminal 1 — backend
cd backend
cp .env.example .env   # set AI_MODEL to a model your endpoint serves
.venv/bin/uvicorn main:app --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev

# Check model is loaded
curl http://localhost:8000/api/health
```

Then in the browser:
1. Load the sample project.
2. Ask: *"What are the main API endpoints?"*
3. Expand the **Reasoning** panel to show Thought → Action → Observation.
4. Click **Export → Markdown** to download the architecture report.
