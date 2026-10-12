'use client';

import { useState } from 'react';
import { BookOpen, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { API } from '@/lib/api';

export default function OverviewPanel({ projectId }: { projectId: string }) {
  const [summary, setSummary] = useState('');
  const [loading, setLoading] = useState(false);

  // On demand: the endpoint spends an LLM call, so it must not fire on mount.
  const generate = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/overview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId }),
      });
      const data = (await res.json()) as { summary?: string };
      setSummary(data.summary || 'No summary returned.');
    } catch {
      setSummary('Overview unavailable — is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-surface">
      <div className="p-3 border-b border shrink-0">
        <button
          type="button"
          onClick={() => void generate()}
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 bg-accent hover:brightness-110 disabled:opacity-50 text-surface-2 rounded-lg px-3 py-2 text-sm font-medium transition"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookOpen className="w-4 h-4" />}
          {loading ? 'Analysing…' : summary ? 'Regenerate overview' : 'Generate overview'}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {summary ? (
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-xs text-muted">
            Summarises the indexed codebase&apos;s architecture. Uses the same model as chat.
          </p>
        )}
      </div>
    </div>
  );
}
