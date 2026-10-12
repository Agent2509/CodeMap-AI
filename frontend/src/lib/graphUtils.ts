import type { GraphLink } from './types';

export const linkSource = (l: GraphLink): string =>
  typeof l.source === 'string' ? l.source : l.source.id;

export const linkTarget = (l: GraphLink): string =>
  typeof l.target === 'string' ? l.target : l.target.id;

export const linkKey = (l: GraphLink): string => `${linkSource(l)}->${linkTarget(l)}`;

export function neighborsOf(id: string, links: GraphLink[]): Set<string> {
  const out = new Set<string>([id]);
  for (const l of links) {
    const s = linkSource(l);
    const t = linkTarget(l);
    if (s === id) out.add(t);
    if (t === id) out.add(s);
  }
  return out;
}

export interface TraceHop {
  source: string;
  target: string;
  key: string;
  line?: number;
  kind?: string;
}

function buildAdjacency(links: GraphLink[]): Map<string, { id: string; key: string; line?: number; kind?: string }[]> {
  const adj = new Map<string, { id: string; key: string; line?: number; kind?: string }[]>();
  const push = (from: string, id: string, link: GraphLink) => {
    let list = adj.get(from);
    if (!list) {
      list = [];
      adj.set(from, list);
    }
    list.push({ id, key: linkKey(link), line: link.line, kind: link.kind });
  };
  for (const l of links) {
    push(linkSource(l), linkTarget(l), l);
    push(linkTarget(l), linkSource(l), l);
  }
  return adj;
}

/** Ordered hops along the shortest path, so callers can show what links each step. */
export function traceHops(start: string, end: string, links: GraphLink[]): TraceHop[] {
  const adj = buildAdjacency(links);
  const prev = new Map<string, { from: string; key: string; line?: number; kind?: string }>();
  const seen = new Set<string>([start]);
  const queue = [start];

  while (queue.length) {
    const current = queue.shift()!;
    if (current === end) break;
    for (const next of adj.get(current) ?? []) {
      if (seen.has(next.id)) continue;
      seen.add(next.id);
      prev.set(next.id, { from: current, key: next.key, line: next.line, kind: next.kind });
      queue.push(next.id);
    }
  }

  if (!seen.has(end)) return [];
  const hops: TraceHop[] = [];
  let cursor = end;
  while (cursor !== start) {
    const step = prev.get(cursor);
    if (!step) return [];
    hops.push({
      source: step.from,
      target: cursor,
      key: step.key,
      line: step.line,
      kind: step.kind,
    });
    cursor = step.from;
  }
  return hops.reverse();
}

export function tracePath(
  start: string,
  end: string,
  links: GraphLink[],
): { nodes: Set<string>; links: Set<string> } {
  const hops = traceHops(start, end, links);
  if (!hops.length) return { nodes: new Set(), links: new Set() };
  const nodes = new Set<string>([start]);
  const pathLinks = new Set<string>();
  for (const hop of hops) {
    nodes.add(hop.source);
    nodes.add(hop.target);
    pathLinks.add(hop.key);
  }
  return { nodes, links: pathLinks };
}
