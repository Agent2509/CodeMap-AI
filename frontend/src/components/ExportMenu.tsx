'use client';

import type { RefObject } from 'react';
import { ChevronDown, FileText, Image as ImageIcon } from 'lucide-react';
import { API } from '@/lib/api';

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ExportMenu({
  projectId,
  svgRef,
}: {
  projectId: string;
  svgRef: RefObject<SVGSVGElement | null>;
}) {
  const exportMarkdown = async () => {
    const res = await fetch(`${API}/api/export/markdown/${projectId}`);
    if (!res.ok) return;
    download(await res.blob(), `${projectId}-ARCHITECTURE.md`);
  };

  const exportSvg = () => {
    const svg = svgRef.current;
    if (!svg) return;
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.style.background = 'var(--color-surface)';
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], {
      type: 'image/svg+xml',
    });
    download(blob, `${projectId}-graph.svg`);
  };

  const item =
    'flex w-full items-center gap-2 text-left px-3 py-2 text-xs text-text hover:bg-surface-2 hover:text-foreground disabled:opacity-40 transition';

  return (
    <details className="relative">
      <summary className="list-none cursor-pointer rounded-lg border border bg-surface-2/80 px-3 py-1.5 text-sm text-foreground hover:border-text hover:text-foreground transition flex items-center gap-1.5">
        Export
        <ChevronDown className="w-3.5 h-3.5 opacity-60" />
      </summary>
      <div className="absolute right-0 z-10 mt-1 w-44 rounded-lg border border bg-surface-2 py-1 text-text shadow-xl overflow-hidden">
        <button className={item} disabled={!projectId} onClick={exportMarkdown}>
          <FileText className="w-3.5 h-3.5" />
          Architecture.md
        </button>
        <button className={item} disabled={!projectId} onClick={exportSvg}>
          <ImageIcon className="w-3.5 h-3.5" />
          Graph SVG
        </button>
      </div>
    </details>
  );
}
