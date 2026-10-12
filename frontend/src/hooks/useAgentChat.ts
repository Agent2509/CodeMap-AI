'use client';

import { useRef, useState } from 'react';
import { API } from '@/lib/api';

export interface AgentMessage {
  role: 'user' | 'agent';
  text: string;
  /** Transient "what the agent is doing" label shown while it works. */
  status?: string;
}

type SSEPayload = { type: string; text?: string; data?: unknown; nodes?: unknown };

function activityLabel(name: string | undefined, args: Record<string, unknown> | undefined): string {
  if (name === 'search_codebase') return 'Searching the codebase…';
  if (name === 'get_file_content') {
    const file = typeof args?.filepath === 'string' ? args.filepath : '';
    return file ? `Reading ${file}…` : 'Reading a file…';
  }
  if (name === 'generate_diagram') return 'Building a diagram…';
  return 'Exploring the codebase…';
}

export function useAgentChat(projectId: string, onHighlight?: (nodeIds: string[]) => void) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const highlightRef = useRef(onHighlight);
  highlightRef.current = onHighlight;

  const sendMessage = async (prompt: string) => {
    const history = messages
      .filter((m) => m.text.trim())
      .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text }));

    setMessages((prev) => [
      ...prev,
      { role: 'user', text: prompt },
      { role: 'agent', text: '' },
    ]);
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, project_id: projectId, history }),
      });
      const reader = res.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          let payload: SSEPayload;
          try {
            payload = JSON.parse(line.slice(5).trim()) as SSEPayload;
          } catch {
            continue;
          }
          // Highlight is a side effect on the parent, not a message update. Calling
          // it from inside a setMessages updater would run during React's render
          // phase and warn about updating another component while rendering.
          if (payload.type === 'highlight') {
            if (Array.isArray(payload.nodes)) {
              highlightRef.current?.(payload.nodes.map(String));
            }
            continue;
          }

          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (!last) return prev;
            const updated = { ...last };
            if (payload.type === 'chunk' && payload.text) {
              updated.text += payload.text;
            } else if (payload.type === 'action') {
              const a = payload.data as
                | { name?: string; arguments?: Record<string, unknown> }
                | undefined;
              updated.status = activityLabel(a?.name, a?.arguments);
            } else if (payload.type === 'observation') {
              // Tool finished; the model is now reasoning over the result.
              updated.status = 'Analyzing…';
            } else if (payload.type === 'thought' && String(payload.text ?? '').startsWith('⏳')) {
              // Retry/backoff notices — surface these, they explain a pause.
              updated.status = String(payload.text);
            } else if (payload.type === 'error') {
              updated.text += `\n\n⚠ ${payload.text ?? 'Agent error'}`;
            }
            next[next.length - 1] = updated;
            return next;
          });
        }
      }
    } finally {
      setLoading(false);
    }
  };

  return { messages, sendMessage, loading };
}
