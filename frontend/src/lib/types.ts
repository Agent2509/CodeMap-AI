export interface GraphNode {
  id: string;
  type: string;
  name: string;
  file_path?: string;
  start_line?: number;
  end_line?: number;
  external?: boolean;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  kind?: string;
  /** Line in the source file that creates this edge, when known. */
  line?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export interface ProjectInfo {
  id: string;
  name: string;
  path: string;
  chunks: number;
}

export interface HubFile {
  id: string;
  name: string;
  imported_by: number;
}

export interface GraphInsights {
  nodes: number;
  links: number;
  entity_counts: Record<string, number>;
  cycles: string[][];
  hubs: HubFile[];
}
