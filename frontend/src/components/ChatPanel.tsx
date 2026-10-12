'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Send, Loader2 } from 'lucide-react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAgentChat } from '@/hooks/useAgentChat';

// Matches citations like `src/app.py`, `app.py:42` or `app.py:42-50`.
const CITATION_RE = /^([\w./@-]+\.\w{1,6})(?::(\d+)(?:-(\d+))?)?$/;

export default function ChatPanel({
  projectId,
  onOpenFile,
  onHighlight,
  externalPrompt,
}: {
  projectId: string;
  onOpenFile?: (path: string, line?: number) => void;
  onHighlight?: (nodeIds: string[]) => void;
  externalPrompt?: { text: string; nonce: number } | null;
}) {
  const { messages, sendMessage, loading } = useAgentChat(projectId, onHighlight);
  const [input, setInput] = useState('');
  const lastPrompt = useRef(0);

  useEffect(() => {
    if (!externalPrompt || externalPrompt.nonce === lastPrompt.current) return;
    lastPrompt.current = externalPrompt.nonce;
    void sendMessage(externalPrompt.text);
  }, [externalPrompt, sendMessage]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    void sendMessage(input);
    setInput('');
  };

  const components: Components = {
    // A wide table must scroll inside its bubble rather than squeeze the panel.
    table({ children }) {
      return (
        <div className="overflow-x-auto my-2">
          <table>{children}</table>
        </div>
      );
    },
    code({ className, children }) {
      const isBlock = typeof className === 'string' && className.startsWith('language-');
      const text = String(children).trim();
      if (!isBlock && onOpenFile) {
        const match = text.match(CITATION_RE);
        if (match) {
          return (
            <button
              type="button"
              onClick={() => onOpenFile(match[1], match[2] ? Number(match[2]) : undefined)}
              className="text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid"
              title="Open in code view"
            >
              {text}
            </button>
          );
        }
      }
      return <code className={className}>{children}</code>;
    },
  };

  return (
    <div className="flex flex-col h-full bg-surface">
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
            <div
              className={
                m.role === 'user'
                  ? 'bg-accent text-surface-2 rounded-xl rounded-br-sm px-3 py-2 max-w-[85%] shadow-sm'
                  : 'bg-surface-2 border rounded-xl rounded-bl-sm px-3 py-2 max-w-[85%] text-text shadow-sm'
              }
            >
              {m.role === 'agent' && loading && !m.text && (
                <div className="flex items-center gap-2 text-xs text-muted">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-accent" />
                  <span>{m.status ?? 'Thinking…'}</span>
                </div>
              )}
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                  {m.text}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={submit} className="flex gap-2 p-3 border-t border bg-surface-2/50">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the code..."
          aria-label="Ask about the code"
          className="flex-grow min-w-0 bg-surface-2 border border rounded-lg px-3 py-2 text-sm placeholder:text-muted text-foreground focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition"
        />
        <button
          type="submit"
          disabled={loading}
          aria-label={loading ? 'Sending message' : 'Send message'}
          className="bg-accent hover:brightness-110 disabled:opacity-50 text-surface-2 rounded-lg px-3 py-2 text-sm font-medium transition flex items-center justify-center"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </button>
      </form>
    </div>
  );
}
