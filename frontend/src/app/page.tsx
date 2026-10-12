'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { Boxes, GitBranch, Loader2, Trash2 } from 'lucide-react';
import { API } from '@/lib/api';
import type { ProjectInfo } from '@/lib/types';

export default function Landing() {
  const router = useRouter();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [projects, setProjects] = useState<ProjectInfo[]>([]);

  const loadProjects = () => {
    fetch(`${API}/api/projects`)
      .then((r) => r.json())
      .then((d) => setProjects(d as ProjectInfo[]))
      .catch(() => setProjects([]));
  };

  useEffect(loadProjects, []);

  const start = async (endpoint: string, body: Record<string, string>, label: string) => {
    setBusy(label);
    setError('');
    try {
      const res = await fetch(`${API}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const { task_id, project_id } = (await res.json()) as {
        task_id: string;
        project_id: string;
      };
      // The workspace owns progress display and resumes this task via ?task=.
      router.push(`/project/${project_id}?task=${task_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not start');
      setBusy(null);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    const isUrl = /^(https?:\/\/|git@)/.test(trimmed);
    return start(
      isUrl ? '/api/clone' : '/api/upload',
      isUrl ? { url: trimmed } : { path: trimmed },
      'analyze',
    );
  };

  const remove = async (project: ProjectInfo) => {
    // Deleting drops the graph and the vector index, so it needs explicit intent —
    // this button is hover-revealed and was easy to hit by accident.
    const ok = window.confirm(
      `Delete “${project.name}”?\n\nThis removes its graph and search index. ` +
        `You can re-analyze the path or URL later.`,
    );
    if (!ok) return;
    await fetch(`${API}/api/projects/${project.id}`, { method: 'DELETE' });
    setProjects((prev) => prev.filter((p) => p.id !== project.id));
  };

  return (
    <div className="min-h-screen bg-background text-text overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-20">
        <div className="flex items-center gap-2 text-accent mb-4">
          <Boxes className="w-5 h-5" />
          <span className="text-xs font-semibold uppercase tracking-widest">CodeMap AI</span>
        </div>
        <h1 className="text-4xl font-semibold tracking-tight text-foreground">
          Understand any codebase
        </h1>
        <p className="mt-3 text-muted max-w-xl">
          Parse a repository into an interactive dependency graph, then ask an agent how it
          actually works — with citations you can click straight into the code.
        </p>

        <form onSubmit={submit} className="mt-8 flex gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="/path/to/project  or  https://github.com/owner/repo"
            className="flex-1 min-w-0 bg-surface-2 border border rounded-lg px-4 py-3 text-sm placeholder:text-muted text-foreground focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent transition"
          />
          <button
            type="submit"
            disabled={!value.trim() || busy !== null}
            className="bg-accent hover:brightness-110 disabled:opacity-50 text-surface-2 font-semibold rounded-lg px-5 py-3 text-sm whitespace-nowrap transition flex items-center gap-2"
          >
            {busy === 'analyze' && <Loader2 className="w-4 h-4 animate-spin" />}
            Analyze
          </button>
        </form>

        <div className="mt-3 flex items-center gap-3 text-xs text-subtle">
          <GitBranch className="w-3.5 h-3.5" />
          <span>Paste a repository URL to clone and map it, or a local directory path.</span>
        </div>

        <div className="mt-6 flex items-center gap-3 text-sm">
          <span className="text-subtle">or</span>
          <button
            onClick={() => start('/api/sample', {}, 'sample')}
            disabled={busy !== null}
            className="border border-strong rounded-lg px-4 py-2 text-foreground hover:border-text disabled:opacity-50 transition flex items-center gap-2"
          >
            {busy === 'sample' && <Loader2 className="w-4 h-4 animate-spin" />}
            Load the sample project
          </button>
        </div>

        {error && (
          <div className="mt-4 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-300">
            {error}
          </div>
        )}

        <div className="mt-16">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle mb-4">
            Recent projects
          </h2>
          {projects.length === 0 ? (
            <p className="text-sm text-subtle">
              Nothing analyzed yet. Load the sample to see how it works.
            </p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {projects.map((p) => (
                <li
                  key={p.id}
                  className="group flex items-center gap-3 rounded-lg border border bg-surface px-4 py-3 hover:border-accent/50 transition"
                >
                  <button
                    onClick={() => router.push(`/project/${p.id}`)}
                    className="flex-1 min-w-0 text-left"
                  >
                    <div className="truncate text-sm font-medium text-foreground">{p.name}</div>
                    <div className="truncate text-xs text-subtle font-mono mt-0.5">
                      {p.path || p.id}
                    </div>
                    <div className="mt-1.5 text-[11px] text-muted">{p.chunks} chunks indexed</div>
                  </button>
                  <button
                    onClick={() => remove(p)}
                    title="Delete project"
                    className="opacity-0 group-hover:opacity-100 text-subtle hover:text-rose-400 transition"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
