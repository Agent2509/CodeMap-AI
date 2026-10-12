'use client';

import { useEffect, useRef, useState } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Sparkles } from 'lucide-react';
import { API } from '@/lib/api';

const LANG_MAP: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  jsx: 'jsx',
  ts: 'typescript',
  tsx: 'tsx',
  json: 'json',
  md: 'markdown',
  css: 'css',
  html: 'html',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
  go: 'go',
  rs: 'rust',
  java: 'java',
  c: 'c',
  cpp: 'cpp',
  h: 'c',
  hpp: 'cpp',
};

function detectLang(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  return LANG_MAP[ext] ?? 'text';
}

export default function CodeViewer({
  projectId,
  path,
  line,
  nodeLabel,
  onExplain,
}: {
  projectId: string;
  path: string;
  line?: number;
  /** Symbol currently being viewed, e.g. "import re"; Explain targets this. */
  nodeLabel?: string;
  onExplain?: (path: string) => void;
}) {
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [resolvedPath, setResolvedPath] = useState(path);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!projectId || !path) {
      setContent('');
      return;
    }
    setError('');
    fetch(`${API}/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`)
      .then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json() as Promise<{ content: string; path: string }>;
      })
      .then((d) => {
        setContent(d.content);
        setResolvedPath(d.path ?? path);
      })
      .catch((e: unknown) => {
        setContent('');
        setError(e instanceof Error ? e.message : 'load failed');
      });
  }, [projectId, path]);

  // Bring the cited line into view once the code has rendered.
  useEffect(() => {
    if (!line || !content || !containerRef.current) return;
    const target = containerRef.current.querySelector('.cmap-highlight');
    target?.scrollIntoView({ block: 'center' });
  }, [content, line]);

  if (!path) {
    return (
      <div className="flex h-full items-center justify-center text-muted text-sm">
        Select a file from the sidebar.
      </div>
    );
  }

  const lang = detectLang(resolvedPath);

  return (
    <div className="h-full flex flex-col bg-surface">
      <div className="px-4 py-2 border-b border bg-surface-2/50 flex items-center gap-3">
        <span className="text-xs text-muted font-mono truncate flex-1">{resolvedPath}</span>
        {nodeLabel && (
          <span className="text-[11px] text-accent truncate max-w-[45%]" title={nodeLabel}>
            {nodeLabel}
          </span>
        )}
        {line ? (
          <span className="text-[10px] uppercase tracking-wide text-accent font-medium">
            L{line}
          </span>
        ) : null}
        <span className="text-[10px] uppercase tracking-wide text-subtle font-medium">{lang}</span>
        {onExplain && (
          <button
            onClick={() => onExplain(resolvedPath)}
            className="flex items-center gap-1.5 text-[11px] rounded-md border border px-2 py-1 text-muted hover:text-accent hover:border-accent/50 transition"
            title="Ask the agent to explain this file"
          >
            <Sparkles className="w-3 h-3" />
            Explain
          </button>
        )}
      </div>
      <div ref={containerRef} className="flex-1 overflow-auto">
        {error ? (
          <p className="p-4 text-xs text-rose-400">{error}</p>
        ) : (
          <SyntaxHighlighter
            language={lang}
            style={vscDarkPlus}
            showLineNumbers
            wrapLines
            wrapLongLines
            lineProps={(n: number) => (n === line ? { className: 'cmap-highlight' } : {})}
            customStyle={{
              margin: 0,
              padding: '1rem',
              background: 'transparent',
              fontSize: '12.5px',
              lineHeight: '1.55',
            }}
            lineNumberStyle={{
              minWidth: '2.5em',
              paddingRight: '1em',
              color: 'var(--color-subtle)',
              fontSize: '12px',
            }}
          >
            {content}
          </SyntaxHighlighter>
        )}
      </div>
    </div>
  );
}
