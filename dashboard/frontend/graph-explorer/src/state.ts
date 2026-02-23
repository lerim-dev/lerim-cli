export type GraphNodeKind = "memory" | "session" | "project" | "tag" | "type";

export interface GraphNode {
  id: string;
  label: string;
  kind: GraphNodeKind;
  score: number;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  weight: number;
  properties: Record<string, unknown>;
}

export interface GraphStats {
  matched_memories: number;
  returned_nodes: number;
  returned_edges: number;
  truncated: boolean;
}

export interface GraphQueryRequest {
  query: string;
  filters: {
    type: string[];
    state: string[];
    projects: string[];
    tags: string[];
  };
  limits: {
    max_nodes: number;
    max_edges: number;
  };
  seed_ids: string[];
  view: "graph" | "table";
}

export interface GraphQueryResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
  warnings: string[];
  cursor: string | null;
}

export interface GraphExpandRequest {
  node_id: string;
  depth: number;
  limits: {
    max_nodes: number;
    max_edges: number;
  };
}

export interface GraphExpandResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: {
    added_nodes: number;
    added_edges: number;
    truncated: boolean;
  };
  warnings: string[];
}

export interface GraphOptionsResponse {
  types: string[];
  states: string[];
  projects: string[];
  tags: string[];
}

export interface ExplorerState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  warnings: string[];
  stats: GraphStats;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  viewMode: "graph" | "table";
  loading: boolean;
}

export const EMPTY_STATS: GraphStats = {
  matched_memories: 0,
  returned_nodes: 0,
  returned_edges: 0,
  truncated: false,
};

export function createInitialState(): ExplorerState {
  return {
    nodes: [],
    edges: [],
    warnings: [],
    stats: { ...EMPTY_STATS },
    selectedNodeId: null,
    selectedEdgeId: null,
    viewMode: "graph",
    loading: false,
  };
}
