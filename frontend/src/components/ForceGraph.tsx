'use client';

import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import * as d3 from 'd3';
import { ChevronDown } from 'lucide-react';
import type { GraphData, GraphInsights, GraphLink, GraphNode } from '@/lib/types';
import { linkKey, linkSource, linkTarget, neighborsOf, traceHops, tracePath, type TraceHop } from '@/lib/graphUtils';

const COLORS: Record<string, string> = {
  file: '#38bdf8',
  class: '#a855f7',
  function: '#10b981',
  import: '#f43f5e',
  module: '#f43f5e',
};

const RADIUS: Record<string, number> = {
  file: 9,
  class: 8,
  function: 6,
  import: 5,
  module: 5,
};

const LEGEND: [string, string][] = [
  ['File', COLORS.file],
  ['Class', COLORS.class],
  ['Function', COLORS.function],
  ['Import', COLORS.import],
];

const shapePath = (type: string, r: number): string => {
  if (type === 'file') {
    const w = r + 3;
    return `M${-w},${-r}h${2 * w}v${2 * r}h${-2 * w}Z`;
  }
  if (type === 'class') {
    return `M0,${-r - 1}L${r + 1},0L0,${r + 1}L${-r - 1},0Z`;
  }
  return `M0,${-r}a${r},${r} 0 1,0 0,${2 * r}a${r},${r} 0 1,0 0,${-2 * r}`;
};

interface GraphApi {
  highlight: (id: string) => void;
  emphasize: (ids: string[]) => void;
  reset: () => void;
  showPath: (a: string, b: string) => boolean;
}

export interface SelectedNode {
  id: string;
  name: string;
  type: string;
}

export default function ForceGraph({
  data,
  svgRef,
  insights,
  onOpenFile,
  onTrace,
  highlightIds,
}: {
  data: GraphData;
  svgRef?: RefObject<SVGSVGElement | null>;
  insights?: GraphInsights | null;
  onOpenFile?: (filePath: string, line?: number, node?: SelectedNode) => void;
  onTrace?: (hops: TraceHop[]) => void;
  /** Node ids to spotlight (e.g. an impact analysis result); empty clears it. */
  highlightIds?: string[];
}) {
  const localRef = useRef<SVGSVGElement>(null);
  const ref = svgRef ?? localRef;
  const tipRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<GraphApi | null>(null);
  const traceRef = useRef(false);
  const traceStartRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [traceMode, setTraceMode] = useState(false);
  const [traceInfo, setTraceInfo] = useState('');
  const [hideExternal, setHideExternal] = useState(false);
  const [fileOnly, setFileOnly] = useState(false);
  const [insightsOpen, setInsightsOpen] = useState(true);
  // Held in a ref so the D3 effect does not re-run when the callback identity changes.
  const openFileRef = useRef(onOpenFile);
  openFileRef.current = onOpenFile;
  const onTraceRef = useRef(onTrace);
  onTraceRef.current = onTrace;
  traceRef.current = traceMode;

  // The rendered view, after the display filters. Kept in a memo so toggles
  // re-run the simulation with a consistent node/link set.
  const view = useMemo(() => {
    let nodes = data.nodes;
    let links = data.links;
    if (hideExternal) nodes = nodes.filter((n) => !n.external);
    if (fileOnly) nodes = nodes.filter((n) => n.type === 'file');
    const ids = new Set(nodes.map((n) => n.id));
    links = links.filter((l) => ids.has(linkSource(l)) && ids.has(linkTarget(l)));
    return { nodes, links };
  }, [data, hideExternal, fileOnly]);

  useEffect(() => {
    if (!ref.current) return;
    const svg = d3.select(ref.current);
    const width = ref.current.clientWidth || 800;
    const height = ref.current.clientHeight || 600;

    const g = svg.append('g');

    const defs = svg.append('defs');

    // Soft node glow filter
    const filter = defs
      .append('filter')
      .attr('id', 'cmap-glow')
      .attr('x', '-50%')
      .attr('y', '-50%')
      .attr('width', '200%')
      .attr('height', '200%');
    filter.append('feGaussianBlur').attr('stdDeviation', '2.8').attr('result', 'coloredBlur');
    filter.append('feMerge').append('feMergeNode').attr('in', 'coloredBlur');
    filter.append('feMerge').append('feMergeNode').attr('in', 'SourceGraphic');

    // Subtle dot-grid background
    const patternSize = 24;
    const dotPattern = defs
      .append('pattern')
      .attr('id', 'cmap-grid')
      .attr('width', patternSize)
      .attr('height', patternSize)
      .attr('patternUnits', 'userSpaceOnUse');
    dotPattern
      .append('circle')
      .attr('cx', patternSize / 2)
      .attr('cy', patternSize / 2)
      .attr('r', 1)
      .attr('fill', 'rgba(255,255,255,0.06)');

    // Arrow marker
    defs
      .append('marker')
      .attr('id', 'cmap-arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 12)
      .attr('refY', 0)
      .attr('markerWidth', 5)
      .attr('markerHeight', 5)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', 'var(--color-muted)');

    // Background grid
    g.append('rect').attr('width', width).attr('height', height).attr('fill', 'url(#cmap-grid)');

    const simulation = d3
      .forceSimulation<GraphNode>(view.nodes)
      .force(
        'link',
        d3
          .forceLink<GraphNode, GraphLink>(view.links)
          .id((d) => d.id)
          .distance(90),
      )
      .force('charge', d3.forceManyBody().strength(-450))
      .force('x', d3.forceX(width / 2).strength(0.08))
      .force('y', d3.forceY(height / 2).strength(0.08))
      .force(
        'collide',
        d3
          .forceCollide<GraphNode>()
          .radius((d) => (RADIUS[d.type] ?? 5) + 18)
          .iterations(2),
      );

    const link = g
      .append('g')
      .selectAll<SVGLineElement, GraphLink>('line')
      .data(view.links)
      .join('line')
      .attr('class', 'cmap-link')
      .attr('stroke', 'var(--color-muted)')
      .attr('stroke-width', 1.5)
      .attr('opacity', 0.35)
      .attr('marker-end', 'url(#cmap-arrow)');

    const nodeG = g
      .append('g')
      .selectAll<SVGGElement, GraphNode>('g')
      .data(view.nodes)
      .join('g')
      .style('cursor', 'pointer');

    const glowShape = nodeG
      .append('path')
      .attr('d', (d) => shapePath(d.type, (RADIUS[d.type] ?? 5) + 4))
      .attr('fill', (d) => COLORS[d.type] ?? '#94a3b8')
      .attr('opacity', 0)
      .attr('filter', 'url(#cmap-glow)');

    const shape = nodeG
      .append('path')
      .attr('d', (d) => shapePath(d.type, RADIUS[d.type] ?? 5))
      .attr('fill', (d) => COLORS[d.type] ?? '#94a3b8')
      .attr('stroke', 'var(--color-background)')
      .attr('stroke-width', 2);

    const label = nodeG
      .append('text')
      .text((d) => d.name)
      .attr('x', (d) => (RADIUS[d.type] ?? 5) + 6)
      .attr('y', 4)
      .attr('font-size', 11)
      .attr('font-weight', 500)
      .attr('fill', 'var(--color-foreground)')
      .attr('opacity', 0.85)
      .attr('pointer-events', 'none');

    const reset = () => {
      nodeG.attr('opacity', 1);
      label.attr('opacity', 0.85);
      link
        .classed('is-active', false)
        .attr('opacity', 0.35)
        .attr('stroke', 'var(--color-muted)')
        .attr('stroke-width', 1.5);
      glowShape.attr('opacity', 0);
    };

    const highlight = (id: string) => {
      const connected = neighborsOf(id, view.links);
      nodeG.attr('opacity', (d) => (connected.has(d.id) ? 1 : 0.12));
      glowShape.attr('opacity', (d) => (connected.has(d.id) ? 0.9 : 0));
      label.attr('opacity', (d) => (connected.has(d.id) ? 0.95 : 0.06));
      link
        .classed('is-active', (l) => linkSource(l) === id || linkTarget(l) === id)
        .attr('opacity', (l) => (linkSource(l) === id || linkTarget(l) === id ? 0.9 : 0.04));
    };

    const showPath = (a: string, b: string): boolean => {
      const result = tracePath(a, b, view.links);
      if (!result.nodes.size) return false;
      nodeG.attr('opacity', (d) => (result.nodes.has(d.id) ? 1 : 0.1));
      glowShape.attr('opacity', (d) => (result.nodes.has(d.id) ? 0.9 : 0));
      label.attr('opacity', (d) => (result.nodes.has(d.id) ? 0.95 : 0.05));
      link
        .classed('is-active', (l) => result.links.has(linkKey(l)))
        .attr('opacity', (l) => (result.links.has(linkKey(l)) ? 0.95 : 0.03))
        .attr('stroke', (l) =>
          result.links.has(linkKey(l)) ? 'var(--color-accent)' : 'var(--color-muted)',
        )
        .attr('stroke-width', (l) => (result.links.has(linkKey(l)) ? 2.5 : 1.5));
      // Report the ordered hops so the shell can show the linking source lines.
      onTraceRef.current?.(traceHops(a, b, view.links));
      return true;
    };

    const emphasize = (ids: string[]) => {
      if (!ids.length) {
        reset();
        return;
      }
      const set = new Set(ids);
      nodeG.attr('opacity', (d) => (set.has(d.id) ? 1 : 0.08));
      glowShape.attr('opacity', (d) => (set.has(d.id) ? 0.9 : 0));
      label.attr('opacity', (d) => (set.has(d.id) ? 0.95 : 0.04));
      link
        .classed('is-active', false)
        .attr('opacity', (l) =>
          set.has(linkSource(l)) && set.has(linkTarget(l)) ? 0.7 : 0.03,
        )
        .attr('stroke', (l) =>
          set.has(linkSource(l)) && set.has(linkTarget(l))
            ? 'var(--color-accent)'
            : 'var(--color-muted)',
        );
    };

    apiRef.current = { highlight, emphasize, reset, showPath };

    const tip = tipRef.current;
    nodeG
      .on('mouseover', (event, d) => {
        shape
          .filter((n) => n.id === d.id)
          .attr('transform', 'scale(1.35)')
          .attr(
            'fill',
            d3.color(COLORS[d.type] ?? '#94a3b8')?.brighter(0.8).toString() ?? '#fff',
          );
        if (!tip) return;
        tip.style.display = 'block';
        tip.style.left = `${event.offsetX + 12}px`;
        tip.style.top = `${event.offsetY + 12}px`;
        tip.textContent = `${d.id} · ${d.type}`;
      })
      .on('mousemove', (event) => {
        if (!tip) return;
        tip.style.left = `${event.offsetX + 12}px`;
        tip.style.top = `${event.offsetY + 12}px`;
      })
      .on('mouseout', (_event, d) => {
        shape
          .filter((n) => n.id === d.id)
          .attr('transform', null)
          .attr('fill', COLORS[d.type] ?? '#94a3b8');
        if (tip) tip.style.display = 'none';
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        if (traceRef.current) {
          if (!traceStartRef.current) {
            traceStartRef.current = d.id;
            setTraceInfo(`Start: ${d.name} — click destination`);
          } else {
            const ok = apiRef.current?.showPath(traceStartRef.current, d.id) ?? false;
            setTraceInfo(ok ? `Path: ${traceStartRef.current} → ${d.id}` : 'No path found');
            traceStartRef.current = null;
          }
          return;
        }
        setSelected(d);
        apiRef.current?.highlight(d.id);
        const file = d.file_path ?? (d.type === 'file' ? d.id : undefined);
        // Pass the node itself so callers can explain the selected symbol rather
        // than only the file it lives in.
        if (file) {
          openFileRef.current?.(file, d.start_line, {
            id: d.id,
            name: d.name,
            type: d.type,
          });
        }
      });

    svg.on('click', () => {
      setSelected(null);
      setTraceInfo('');
      traceStartRef.current = null;
      apiRef.current?.reset();
      onTraceRef.current?.([]);
    });

    nodeG.call(
      d3
        .drag<SVGGElement, GraphNode>()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on('drag', (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on('end', (event, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        }),
    );

    const arrowEnd = (d: GraphLink, axis: 'x' | 'y'): number => {
      if (typeof d.source === 'string' || typeof d.target === 'string') return 0;
      const x1 = d.source.x ?? 0;
      const y1 = d.source.y ?? 0;
      const x2 = d.target.x ?? 0;
      const y2 = d.target.y ?? 0;
      const radius = (RADIUS[d.target.type] ?? 5) + 11;
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy) || 1;
      return axis === 'x' ? x2 - (dx / len) * radius : y2 - (dy / len) * radius;
    };

    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (typeof d.source === 'string' ? 0 : d.source.x ?? 0))
        .attr('y1', (d) => (typeof d.source === 'string' ? 0 : d.source.y ?? 0))
        .attr('x2', (d) => arrowEnd(d, 'x'))
        .attr('y2', (d) => arrowEnd(d, 'y'));
      nodeG.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    // Legend card
    const legend = svg.append('g').attr('transform', 'translate(14,14)');
    const legendItemCount = LEGEND.length;
    const cardWidth = 110;
    const cardHeight = 16 + legendItemCount * 20;
    legend
      .append('rect')
      .attr('width', cardWidth)
      .attr('height', cardHeight)
      .attr('rx', 8)
      .attr('fill', 'var(--color-surface-2)')
      .attr('stroke', 'var(--color-border)')
      .attr('opacity', 0.95);
    LEGEND.forEach(([name, color], i) => {
      const y = 12 + i * 20;
      legend.append('circle').attr('r', 5).attr('cx', 14).attr('cy', y).attr('fill', color);
      legend
        .append('text')
        .attr('x', 26)
        .attr('y', y + 4)
        .attr('font-size', 11)
        .attr('fill', 'var(--color-muted)')
        .text(name);
    });

    // Zoom
    svg.call(
      d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 8])
        .on('zoom', (event) => g.attr('transform', event.transform.toString())),
    );

    return () => {
      simulation.stop();
      d3.select(ref.current).selectAll('*').remove();
      apiRef.current = null;
    };
  }, [view, ref]);

  // Spotlight an external set of nodes (an impact analysis result) once the
  // simulation for the current view has been built.
  useEffect(() => {
    if (!highlightIds) return;
    apiRef.current?.emphasize(highlightIds);
  }, [highlightIds, view]);

  return (
    <div className="relative w-full h-full">
      <svg
        ref={ref}
        role="img"
        aria-label={`Dependency graph: ${data?.nodes?.length ?? 0} nodes, ${
          data?.links?.length ?? 0
        } links. Use the Code panel for a readable list.`}
        className="w-full h-full bg-surface"
      />

      <div className="absolute top-3 right-3 flex items-center gap-2">
        <div className="flex rounded-md border border bg-surface-2/90 p-0.5 text-xs">
          {([['Entities', false], ['Files', true]] as const).map(([label, only]) => (
            <button
              key={label}
              onClick={() => setFileOnly(only)}
              className={`px-2.5 py-1 rounded transition ${
                fileOnly === only ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setHideExternal((v) => !v)}
          className={`text-xs px-3 py-1.5 rounded-md border transition ${
            hideExternal
              ? 'bg-accent/15 border-accent text-accent'
              : 'bg-surface-2/90 border text-muted hover:text-text'
          }`}
        >
          Hide external
        </button>
        <button
          onClick={() => setTraceMode((v) => !v)}
          className={`text-xs px-3 py-1.5 rounded-md border transition ${
            traceMode
              ? 'bg-accent/15 border-accent text-accent'
              : 'bg-surface-2/90 border text-muted hover:text-text'
          }`}
        >
          Trace path
        </button>
      </div>

      {traceInfo && (
        <div className="absolute top-14 right-3 bg-surface-2 border rounded-md px-3 py-1.5 text-xs text-text shadow-lg">
          {traceInfo}
        </div>
      )}

      {insights && insights.nodes > 0 && (
        <div className="absolute bottom-4 right-4 w-60 bg-surface-2/95 border rounded-lg text-xs text-text shadow-xl overflow-hidden">
          <button
            onClick={() => setInsightsOpen((v) => !v)}
            title={insightsOpen ? 'Collapse insights' : 'Expand insights'}
            className="w-full flex items-center justify-between px-3 py-2 hover:bg-surface-3/50 transition"
          >
            <span className="text-[10px] uppercase tracking-wider text-subtle font-semibold">
              Graph insights
            </span>
            <ChevronDown
              className={`w-3.5 h-3.5 text-subtle transition ${insightsOpen ? '' : '-rotate-90'}`}
            />
          </button>
          {insightsOpen && (
            <div className="px-3 pb-3 space-y-2">
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted">
                {Object.entries(insights.entity_counts).map(([type, count]) => (
                  <span key={type}>
                    <span className="text-foreground font-medium">{count}</span> {type}
                  </span>
                ))}
              </div>
              {insights.hubs.length > 0 && (
                <div>
                  <div className="text-muted mb-1">Most depended-on</div>
                  <ul className="space-y-0.5">
                    {insights.hubs.slice(0, 3).map((hub) => (
                      <li key={hub.id} className="flex justify-between gap-2">
                        <span className="font-mono truncate">{hub.name}</span>
                        <span className="text-subtle">{hub.imported_by}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {insights.cycles.length > 0 && (
                <div className="text-amber-400">
                  {insights.cycles.length} circular dependenc
                  {insights.cycles.length === 1 ? 'y' : 'ies'}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {selected && (
        <div className="absolute bottom-4 left-4 w-80 bg-surface-2 border rounded-lg p-3 text-xs text-text shadow-xl">
          <div className="font-mono break-all text-foreground font-medium">{selected.id}</div>
          <div className="mt-1 text-muted">
            {selected.type}
            {selected.file_path && ` · ${selected.file_path}`}
            {selected.start_line != null && ` · L${selected.start_line}-${selected.end_line}`}
          </div>
          <button
            onClick={() => {
              setSelected(null);
              apiRef.current?.reset();
            }}
            className="mt-3 text-muted hover:text-foreground text-[11px] uppercase tracking-wide"
          >
            Clear
          </button>
        </div>
      )}

      <div
        ref={tipRef}
        style={{ display: 'none' }}
        className="absolute pointer-events-none bg-surface-2 border rounded-md px-2 py-1 text-xs text-foreground shadow-lg"
      />
    </div>
  );
}
