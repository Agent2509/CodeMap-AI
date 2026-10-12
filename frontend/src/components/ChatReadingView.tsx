'use client';

import { useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import ChatPanel from '@/components/ChatPanel';

export default function ChatReadingView({ projectId }: { projectId: string }) {
  const router = useRouter();

  // Citations still jump to the code: route back to the workspace with the file
  // and line as query params, where the Workspace opens the Code tab on mount.
  const openFile = (path: string, line?: number) => {
    const query = new URLSearchParams({ file: path });
    if (line) query.set('line', String(line));
    router.push(`/project/${projectId}?${query.toString()}`);
  };

  return (
    <div className="flex flex-col h-screen bg-background text-text overflow-hidden">
      <header className="h-14 shrink-0 flex items-center gap-3 px-4 border-b border bg-surface/80 backdrop-blur-md">
        <button
          onClick={() => router.push(`/project/${projectId}`)}
          className="flex items-center gap-1.5 text-sm text-muted hover:text-foreground transition"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to workspace
        </button>
        <span className="text-[10px] uppercase tracking-wider text-subtle font-semibold">
          Reading view
        </span>
      </header>
      <main className="flex-1 overflow-hidden">
        <ChatPanel projectId={projectId} onOpenFile={openFile} />
      </main>
    </div>
  );
}
