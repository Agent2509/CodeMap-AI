'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Maximize2, Minimize2, Search, Sparkles, X } from 'lucide-react';
import ChatPanel from '@/components/ChatPanel';
import CodeViewer from '@/components/CodeViewer';
import ExportMenu from '@/components/ExportMenu';
import ForceGraph, { type SelectedNode } from '@/components/ForceGraph';
import OverviewPanel from '@/components/OverviewPanel';
import { API } from '@/lib/api';
import type { TraceHop } from '@/lib/graphUtils';
import type { GraphData, GraphInsights, ProjectInfo } from '@/lib/types';

function waitForIndex(taskId: string, onProgress: (value: number) => void): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const es = new EventSource(`${API}/api/upload/status/${taskId}`);
    // The stream ends normally once the task reports done/error. EventSource treats
    // that close as an error and would reconnect forever, so shut it down first.
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      es.close();
      fn();
    };
    es.onmessage = (ev) => {
      const d = JSON.parse(ev.data) as { status: string; progress?: number; error?: string };
      if (d.progress !== undefined) onProgress(d.progress);
      if (d.status === 'done') {
        finish(resolve);
      } else if (d.status === 'error') {
        // "unknown task" means the server restarted or the task already finished —
        // the index is on disk either way, so do not treat it as a failure.
        if (d.error === 'unknown task') finish(resolve);
        else finish(() => reject(new Error(d.error ?? 'index failed')));
      }
    };
    es.onerror = () => finish(() => reject(new Error('status stream failed')));
  });
}

/** File behind a node id. Entity ids look like "<path>:<type>:<name>". */
const fileOf = (nodeId: string) => (nodeId.includes(':') ? nodeId.split(':')[0] : nodeId);
const shortName = (nodeId: string) => {
  const file = fileOf(nodeId);
  const base = file.split('/').pop() ?? file;
  const name = nodeId.includes(':') ? nodeId.split(':').slice(2).join(':') : '';
  return name ? `${base} · ${name}` : base;
};

export default function Workspace({
  projectId,
  initialFile,
  initialLine,
  task,
}: {
  projectId: string;
  initialFile?: string;
  initialLine?: number;
  task?: string;
}) {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(Boolean(task));
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [activeFile, setActiveFile] = useState(initialFile ?? '');
  const [activeLine, setActiveLine] = useState<number | undefined>(initialLine);
  // Which symbol is open, when the file was reached by clicking a node or hop.
  // Explain targets this rather than the whole file.
  const [activeNode, setActiveNode] = useState<SelectedNode | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [expandCode, setExpandCode] = useState(false);
  const [hooks, setHops] = useState<TraceHop[]>([]);
  const [highlightNodes, setHighlightNodes] = useState<string[]>([]);
  const [hopLines, setHopLines] = useState<Record<number, string>>({});
  const [aiPrompt, setAiPrompt] = useState<{ text: string; nonce: number } | null>(null);
  const [insights, setInsights] = useState<GraphInsights | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [panel, setPanel] = useState<'chat' | 'overview'>('chat');
  const svgRef = useRef<SVGSVGElement>(null);

  const fetchProjectData = async (id: string): Promise<GraphData | null> => {
    try {
      const res = await fetch(`${API}/api/graph?project_id=${id}`);
      const graph = (await res.json()) as GraphData;
      setGraphData(graph);
      const ins = await fetch(`${API}/api/graph/insights?project_id=${id}`);
      setInsights(ins.ok ? ((await ins.json()) as GraphInsights) : null);
      if (graph?.nodes?.length) setError('');
      return graph;
    } catch (e) {
      console.error('Failed to fetch project data', e);
      return null;
    }
  };

  useEffect(() => {
    fetch(`${API}/api/projects`)
      .then((r) => r.json())
      .then((d) => setProjects(d as ProjectInfo[]))
      .catch(() => setProjects([]));
  }, [refreshKey]);

  // The file list backs the search box. meta.json lists every indexed file, so
  // search reaches README and config files the graph has no node for.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/projects/${projectId}/tree`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        const flatten = (nodes: typeof d.tree, prefix: string): string[] =>
          (nodes ?? []).flatMap((node: { name: string; type: string; children?: never[] }) => {
            const path = prefix ? `${prefix}/${node.name}` : node.name;
            return node.type === 'dir' ? flatten(node.children, path) : [path];
          });
        setFiles(flatten(d.tree, ''));
      })
      .catch(() => setFiles([]));
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Indexing runs server-side and writes to disk regardless of the status
      // stream, so a dropped stream must not be reported as a failure when the
      // graph actually landed (e.g. a stale ?task= from a restarted server).
      let streamError: Error | null = null;
      if (task) {
        try {
          await waitForIndex(task, setProgress);
        } catch (e) {
          streamError = e instanceof Error ? e : new Error('indexing failed');
        }
      }
      if (cancelled) return;
      setLoading(false);
      setRefreshKey((k) => k + 1);
      const graph = await fetchProjectData(projectId);
      if (cancelled) return;

      if (!graph?.nodes?.length) {
        if (streamError) setError(streamError.message);
      } else {
        // The graph is there, so nothing failed the user needs to see. This also
        // clears a stale error left over from a previous project or a dropped stream.
        setError('');
        if (task) router.replace(`/project/${projectId}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, task, router]);

  // Pull the source line each trace hop points at, so the connections list shows
  // the actual statement that creates the link.
  useEffect(() => {
    if (!hooks.length) {
      setHopLines({});
      return;
    }
    let cancelled = false;
    void (async () => {
      const found: Record<number, string> = {};
      await Promise.all(
        hooks.map(async (hop, index) => {
          if (hop.line == null) return;
          try {
            const res = await fetch(
              `${API}/api/projects/${projectId}/file?path=${encodeURIComponent(fileOf(hop.source))}`,
            );
            if (!res.ok) return;
            const body = (await res.json()) as { content: string };
            const text = body.content.split('\n')[hop.line - 1];
            if (text != null) found[index] = text.trim();
          } catch {
            /* a missing line just renders blank */
          }
        }),
      );
      if (!cancelled) setHopLines(found);
    })();
    return () => {
      cancelled = true;
    };
  }, [hooks, projectId]);

  const openFile = (filePath: string, line?: number, node?: SelectedNode) => {
    setActiveFile(filePath);
    setActiveLine(line);
    setActiveNode(node ?? null);
  };

  /** Turn a trace hop's source node id into the node it refers to. */
  const nodeFromId = (id: string): SelectedNode | null => {
    const found = graphData?.nodes.find((n) => n.id === id);
    if (!found) return null;
    return { id: found.id, name: found.name, type: found.type };
  };

  /** Explain how the traced path connects its endpoints, and why. */
  const explainConnection = () => {
    if (!hooks.length) return;
    const start = hooks[0].source;
    const end = hooks[hooks.length - 1].target;
    const chain = hooks
      .map((hop) => {
        const at = hop.line != null ? ` (${fileOf(hop.source)}:${hop.line})` : '';
        return `  ${shortName(hop.source)}${at} --${hop.kind ?? 'link'}--> ${shortName(hop.target)}`;
      })
      .join('\n');

    setAiPrompt({
      text:
        `Explain how ${shortName(start)} connects to ${shortName(end)} in this codebase, ` +
        `and why they depend on each other. The traced path is:\n${chain}\n\n` +
        `Read the linking lines if useful. Keep it to a short summary and a few bullets.`,
      nonce: Date.now(),
    });
  };

  const explainFile = (filePath: string) => {
    // Prefer the selected symbol: clicking an import node should explain that
    // import, not the entire file it lives in.
    if (activeNode && activeNode.type !== 'file') {
      const kind =
        activeNode.type === 'function'
          ? 'function'
          : activeNode.type === 'class'
            ? 'class'
            : activeNode.type === 'import'
              ? 'import'
              : activeNode.type;
      const where = activeLine ? `${filePath}:${activeLine}` : filePath;
      setAiPrompt({
        text:
          `Briefly explain the ${kind} \`${activeNode.name}\` in \`${where}\`: what it does ` +
          `and the key things to know. A short summary and a few bullets — no full report.`,
        nonce: Date.now(),
      });
      return;
    }
    setAiPrompt({
      text:
        `Briefly explain ${filePath}: what it does and the key things to know about it. ` +
        `A short summary and a few bullets — no full report.`,
      nonce: Date.now(),
    });
  };

  const showGraph = !expandCode;
  const showSide = expandCode || hooks.length > 0 || Boolean(activeFile);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    const starts: string[] = [];
    const contains: string[] = [];
    for (const file of files) {
      const lower = file.toLowerCase();
      if (lower.endsWith('/' + needle) || lower.startsWith(needle)) starts.push(file);
      else if (lower.includes(needle)) contains.push(file);
    }
    return [...starts, ...contains].slice(0, 8);
  }, [query, files]);

  return (
    <div className="flex flex-col h-screen bg-background text-text overflow-hidden">
      {/* The workspace had no h1 at all, leaving the document without a heading. */}
      <h1 className="sr-only">
        {projects.find((p) => p.id === projectId)?.name ?? 'Project'} — CodeMap AI
      </h1>
      <header className="h-14 shrink-0 flex items-center gap-3 px-3 border-b border bg-surface/80 backdrop-blur-md">
        <button
          onClick={() => router.push('/')}
          className="text-sm font-semibold tracking-tight text-foreground whitespace-nowrap hover:text-accent transition"
          title="All projects"
        >
          CodeMap AI
        </button>
        <nav className="flex items-center gap-1.5 overflow-x-auto min-w-0">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => router.push(`/project/${p.id}`)}
              className={`px-2.5 py-1 rounded-md text-xs whitespace-nowrap border transition ${
                p.id === projectId
                  ? 'border-accent/50 text-accent bg-accent/10'
                  : 'border text-muted hover:text-text'
              }`}
            >
              {p.name}
            </button>
          ))}
        </nav>

        <div className="relative shrink-0 hidden sm:block">
          <div className="flex items-center gap-1.5 bg-surface-2 border border rounded-md px-2 py-1">
            <Search className="w-3.5 h-3.5 text-subtle shrink-0" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && matches[0]) {
                  openFile(matches[0]);
                  setQuery('');
                } else if (e.key === 'Escape') {
                  setQuery('');
                }
              }}
              placeholder="Find file…"
              aria-label="Find a file"
              className="w-36 bg-transparent text-xs text-foreground placeholder:text-subtle rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            />
          </div>
          {query.trim() && (
            <ul className="absolute left-0 top-full mt-1 z-20 w-80 max-h-72 overflow-y-auto rounded-md border border bg-surface-2 shadow-xl">
              {matches.length === 0 ? (
                <li className="px-3 py-2 text-xs text-subtle">No matching files.</li>
              ) : (
                matches.map((file) => (
                  <li key={file}>
                    <button
                      onClick={() => {
                        openFile(file);
                        setQuery('');
                      }}
                      className="w-full text-left px-3 py-1.5 text-xs font-mono text-muted hover:text-foreground hover:bg-surface-3 transition truncate"
                      title={file}
                    >
                      {file}
                    </button>
                  </li>
                ))
              )}
            </ul>
          )}
        </div>

        {error && (
          <span className="flex items-center gap-1 text-xs text-rose-400 truncate min-w-0">
            <span className="truncate">{error}</span>
            <button
              onClick={() => setError('')}
              title="Dismiss"
              className="shrink-0 text-subtle hover:text-text"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        )}
        {loading && <span className="text-xs text-muted whitespace-nowrap">Indexing {progress}%</span>}
        <div className="flex-1" />
        <button
          onClick={() => router.push(`/project/${projectId}/chat`)}
          title="Open chat in a full-width reading view"
          className="p-1.5 rounded-md border text-muted hover:text-text transition"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
        <ExportMenu projectId={projectId} svgRef={svgRef} />
      </header>

      <div
        className="grid flex-1 overflow-hidden grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-cols-[1fr_minmax(320px,30%)] lg:grid-rows-1"
      >
        <main className="flex overflow-hidden bg-surface">
          {showGraph && (
            <div className="flex-1 min-w-0">
              {graphData ? (
                <ForceGraph
                  data={graphData}
                  svgRef={svgRef}
                  insights={insights}
                  onOpenFile={openFile}
                  onTrace={setHops}
                  highlightIds={highlightNodes}
                />
              ) : (
                <div className="flex h-full items-center justify-center text-muted text-sm">
                  {loading ? 'Analyzing codebase...' : 'No graph yet.'}
                </div>
              )}
            </div>
          )}

          {showSide && (
            <div
              className={`${
                expandCode ? 'flex-1' : 'w-[48%] min-w-[360px]'
              } border-l border flex flex-col overflow-hidden`}
            >
              <div className="flex items-center gap-2 px-2 py-1 border-b border bg-surface-2/50">
                <span className="text-[10px] uppercase tracking-wider text-subtle font-semibold flex-1 truncate">
                  {hooks.length ? `Connections (${hooks.length})` : 'Code'}
                </span>
                {hooks.length > 0 && (
                  <button
                    onClick={explainConnection}
                    title="Ask the agent how these are connected and why"
                    className="flex items-center gap-1.5 text-[11px] rounded-md border border px-2 py-0.5 text-muted hover:text-accent hover:border-accent/50 transition"
                  >
                    <Sparkles className="w-3 h-3" />
                    Explain
                  </button>
                )}
                <button
                  onClick={() => setExpandCode((v) => !v)}
                  title={expandCode ? 'Show graph again' : 'Expand to full width'}
                  className="p-1 rounded text-subtle hover:text-text transition"
                >
                  {expandCode ? (
                    <Minimize2 className="w-3.5 h-3.5" />
                  ) : (
                    <Maximize2 className="w-3.5 h-3.5" />
                  )}
                </button>
                <button
                  onClick={() => {
                    setActiveFile('');
                    setActiveNode(null);
                    setHops([]);
                  }}
                  title="Close panel"
                  className="p-1 rounded text-subtle hover:text-text transition"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              {hooks.length > 0 ? (
                <ul className="flex-1 overflow-y-auto divide-y divide-[var(--border)]">
                  {hooks.map((hop, index) => (
                    <li key={index}>
                      <button
                        onClick={() => {
                          openFile(
                            fileOf(hop.source),
                            hop.line,
                            nodeFromId(hop.source) ?? undefined,
                          );
                          setHops([]);
                        }}
                        className="w-full text-left px-3 py-2 hover:bg-surface-2 transition"
                      >
                        <div className="flex items-center gap-1.5 text-[11px] text-muted">
                          <span className="font-mono truncate">{shortName(hop.source)}</span>
                          {hop.line != null && <span className="text-accent">:{hop.line}</span>}
                          <ArrowRight className="w-3 h-3 shrink-0" />
                          <span className="font-mono truncate">{shortName(hop.target)}</span>
                          {hop.kind && (
                            <span className="ml-auto text-[10px] text-subtle shrink-0">
                              {hop.kind}
                            </span>
                          )}
                        </div>
                        <pre className="mt-1 text-[11px] font-mono text-foreground/80 whitespace-pre-wrap break-all">
                          {hopLines[index] ?? '…'}
                        </pre>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex-1 overflow-hidden">
                  <CodeViewer
                    projectId={projectId}
                    path={activeFile}
                    line={activeLine}
                    nodeLabel={activeNode ? `${activeNode.type} ${activeNode.name}` : undefined}
                    onExplain={explainFile}
                  />
                </div>
              )}
            </div>
          )}
        </main>

        <aside className="overflow-hidden bg-surface-2 border-l border flex flex-col">
          <div className="flex border-b border shrink-0">
            {(['chat', 'overview'] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setPanel(tab)}
                className={`flex-1 px-3 py-2 text-xs font-medium capitalize transition border-b-2 ${
                  panel === tab
                    ? 'text-accent border-accent'
                    : 'text-muted border-transparent hover:text-text'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
          <div className="flex-1 min-h-0">
            {panel === 'chat' ? (
              <ChatPanel
                projectId={projectId}
                onOpenFile={openFile}
                onHighlight={(ids) => {
                  // Show the blast radius on the graph: clear any side panel so the
                  // graph is fully visible while it is lit up.
                  setActiveFile('');
                  setHops([]);
                  setHighlightNodes(ids);
                }}
                externalPrompt={aiPrompt}
              />
            ) : (
              <OverviewPanel projectId={projectId} />
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
