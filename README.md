# CodeMap AI

CodeMap AI is a local, agentic, RAG-powered codebase exploration IDE. It parses any codebase into an Abstract Syntax Tree (AST), builds an interactive dependency graph, and provides an integrated AI chat agent that can semantically search and reason about the architecture in real-time.

## 🚀 Key Features

- **3-Column Enterprise IDE**: A Vercel-styled interface featuring a sidebar for project management, a central pane for D3 Force Graphs / Live Code, and a side panel for AI chat and codebase overviews.
- **AST-Aware Chunking & RAG**: Uses `tree-sitter` to parse 7 languages (Python, JS/TS, Java, Go, Rust, C/C++) semantically rather than chunking by line count, then retrieves with hybrid search (Jina embeddings + BM25, fused by reciprocal rank fusion) over a local `ChromaDB`.
- **Graph Intelligence**: Beyond file dependencies, the graph carries an entity-level call graph, cycle detection and centrality, so the UI can surface circular dependencies and the most depended-on files.
- **Blast Radius**: Ask "what breaks if I change this?" and the agent walks the reverse-dependency closure, then lights up exactly those nodes on the graph.
- **Agentic Reasoning Stream**: The integrated chat agent (powered by any OpenAI-compatible endpoint — Groq, DeepSeek, or local LM Studio) exposes its ReAct (Reasoning & Action) loop, streaming its thoughts, tool executions, and findings directly to the UI.
- **Interactive Graph**: A D3.js force-directed graph with custom entity shapes, shortest-path BFS tracing (Trace Path), and click-to-dim exploration.
- **Local Path or GitHub URL**: Index a directory on disk, or paste a repository URL and the backend clones and indexes it.
- **Multi-Project Isolation**: Upload multiple projects simultaneously. The backend securely isolates graph data and vector collections.

## 🏗️ Architecture

- **Frontend:** Next.js 16, React 19, Tailwind CSS v4, D3.js, React Markdown.
- **Backend:** FastAPI (Python 3.12), SQLite (Sample App).
- **AI / Pipeline:** `tree-sitter` (multi-language AST), ChromaDB (Vector Store), Jina Embeddings (161M) + BM25 (hybrid retrieval), `openai` python SDK (Agent Tool Calling).
- **Streaming:** Server-Sent Events (SSE) used for both the large-scale repository indexing process and the AI chat responses to prevent browser timeouts.

## 🛠️ Setup & Execution

### 1. Start the Backend (FastAPI + ChromaDB)
The backend requires `uv` or `pip` to install dependencies.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Configure the LLM. Copy the template, then keep ONE provider block uncommented
# and put a real key/model in it (the default template targets Groq).
cp .env.example .env
# Exclude runtime dirs (clones/, chroma_data/, projects/) from the reload watcher:
# cloning/indexing writes there and would otherwise restart the server mid-request.
uvicorn main:app --reload --port 8000 \
  --reload-exclude "$PWD/clones" \
  --reload-exclude "$PWD/chroma_data" \
  --reload-exclude "$PWD/projects" \
  --reload-exclude "$PWD/.venv"
```

`.env` is the single source of truth for LLM config and overrides any `AI_*`
variables already exported in your shell, so the base URL, key and model always
move together. Check the provider and model are live before the demo:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","model":"openai/gpt-oss-120b","llm_reachable":true}
```

Supported providers (all OpenAI-compatible): **Groq** (hosted, fastest),
**DeepSeek** (hosted), and **LM Studio** (local, offline). See `.env.example`.

Optionally set `AI_FALLBACK_*` to a second endpoint. When the primary returns a
rate-limit error — free tiers cap out quickly — the agent switches to the
fallback instead of failing, so a demo survives a spent quota.

### 2. Start the Frontend (Next.js)
```bash
cd frontend
npm install
npm run dev
```

### 3. Usage
1. Open `http://localhost:3000`.
2. Click **Load Sample** to instantly boot the sample database, paste a local directory path, or paste a GitHub URL to clone and analyse it.
3. Switch between **Graph** and **Code** views in the center console.
4. Use the Chat Panel to ask the agent specific architectural questions.

## 🧪 Testing

The backend is verified with a 54-test `pytest` suite covering RAG retrieval (hybrid + dedupe), chunking, multi-language parsing, graph generation (typed edges, call graph, cycles, hubs), import resolution, impact analysis, path traversal guards, the agent tools, and the ReAct loop including provider-failure recovery.

```bash
cd backend
.venv/bin/pytest tests/
```
