import cytoscape, { type CollectionReturnValue, type Core, type ElementDefinition, type EventObjectNode } from "cytoscape";
import cytoscapeElk from "cytoscape-elk";
import cytoscapeFcose from "cytoscape-fcose";
import type { GraphEdge, GraphNode } from "../state";

const elkPlugin = (cytoscapeElk as { default?: unknown }).default ?? cytoscapeElk;
const fcosePlugin = (cytoscapeFcose as { default?: unknown }).default ?? cytoscapeFcose;
cytoscape.use(elkPlugin as never);
cytoscape.use(fcosePlugin as never);

type LayoutMode = "force" | "layered";

export const KIND_COLOR: Record<string, string> = {
  memory: "#22d3ee",
  session: "#60a5fa",
  project: "#f59e0b",
  tag: "#a78bfa",
  type: "#fb7185",
};

const EDGE_COLOR: Record<string, string> = {
  from_session: "rgba(96, 165, 250, 0.5)",
  in_project: "rgba(245, 158, 11, 0.55)",
  tagged: "rgba(167, 139, 250, 0.5)",
  typed_as: "rgba(251, 113, 133, 0.48)",
};

const KIND_SHAPE: Record<string, string> = {
  memory: "round-rectangle",
  session: "ellipse",
  project: "hexagon",
  tag: "diamond",
  type: "rectangle",
};

export interface SelectionSnapshot {
  selectedNode: GraphNode | null;
  selectedEdge: GraphEdge | null;
  connectedNodes: GraphNode[];
  outgoing: Array<{ kind: string; count: number }>;
  incoming: Array<{ kind: string; count: number }>;
}

function toElement(node: GraphNode): ElementDefinition {
  const label = node.kind === "memory" ? node.label : "";
  return {
    data: {
      id: node.id,
      label,
      fullLabel: node.label,
      kind: node.kind,
      score: node.score,
      properties: node.properties,
    },
    classes: node.kind,
  };
}

function toEdge(edge: GraphEdge): ElementDefinition {
  return {
    data: {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: edge.kind,
      weight: edge.weight,
      properties: edge.properties,
    },
  };
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

interface ViewportSnapshot {
  zoom: number;
  pan: { x: number; y: number };
}

function captureViewport(cy: Core): ViewportSnapshot {
  const pan = cy.pan();
  return {
    zoom: cy.zoom(),
    pan: { x: pan.x, y: pan.y },
  };
}

export class GraphRenderer {
  private readonly cy: Core;

  private fullNodes = new Map<string, GraphNode>();

  private fullEdges = new Map<string, GraphEdge>();

  private activeNodeId: string | null = null;

  private layoutMode: LayoutMode = "force";

  private readonly onSelectionChanged: (snapshot: SelectionSnapshot) => void;

  private readonly onNodeDoubleClick?: (nodeId: string) => void;

  constructor(
    el: HTMLElement,
    onSelectionChanged: (snapshot: SelectionSnapshot) => void,
    onNodeDoubleClick?: (nodeId: string) => void,
  ) {
    this.onSelectionChanged = onSelectionChanged;
    this.onNodeDoubleClick = onNodeDoubleClick;
    this.cy = cytoscape({
      container: el,
      elements: [],
      minZoom: 0.2,
      maxZoom: 3.2,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele) => KIND_COLOR[String(ele.data("kind"))] || "#8ea0b8",
            label: "data(label)",
            color: "#dff2ff",
            "font-size": 10,
            "text-wrap": "wrap",
            "text-max-width": 180,
            "text-valign": "bottom",
            "text-margin-y": 7,
            shape: (ele) => KIND_SHAPE[String(ele.data("kind"))] || "ellipse",
            "border-width": 1.8,
            "border-color": "rgba(226, 238, 252, 0.52)",
            "text-background-opacity": 0.16,
            "text-background-color": "#08101b",
            "text-background-padding": "2px",
            width: (ele) => {
              const kind = String(ele.data("kind"));
              const score = Number(ele.data("score") || 0);
              const base = kind === "memory" ? 24 : 16;
              return base + Math.min(14, score * 12);
            },
            height: (ele) => {
              const kind = String(ele.data("kind"));
              const score = Number(ele.data("score") || 0);
              const base = kind === "memory" ? 24 : 16;
              return base + Math.min(14, score * 12);
            },
          },
        },
        {
          selector: "node.memory",
          style: {
            "font-size": 11.5,
            "font-weight": 600,
            "text-max-width": 220,
            "border-color": "rgba(103, 232, 249, 0.82)",
          },
        },
        {
          selector: "node.project",
          style: {
            "border-color": "rgba(251, 191, 36, 0.8)",
          },
        },
        {
          selector: "node.tag",
          style: {
            "border-color": "rgba(196, 181, 253, 0.8)",
          },
        },
        {
          selector: "node.type",
          style: {
            "border-color": "rgba(251, 113, 133, 0.8)",
          },
        },
        {
          selector: "node.pinned",
          style: {
            "border-width": 2.2,
            "border-color": "#f8fafc",
          },
        },
        {
          selector: "node.search-hit",
          style: {
            "overlay-opacity": 0.15,
            "overlay-color": "#22d3ee",
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele) => {
              const weight = Number(ele.data("weight") || 1);
              return Math.min(2.1, 0.9 + weight * 0.6);
            },
            "line-color": (ele) => EDGE_COLOR[String(ele.data("kind"))] || "rgba(148,163,184,0.5)",
            "target-arrow-color": (ele) => EDGE_COLOR[String(ele.data("kind"))] || "rgba(148,163,184,0.6)",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.56,
            "line-opacity": 0.7,
          },
        },
        {
          selector: "edge:selected",
          style: {
            width: 2.4,
            "line-color": "#f59e0b",
            "target-arrow-color": "#f59e0b",
          },
        },
        {
          selector: "node.muted",
          style: {
            opacity: 0.18,
            "text-opacity": 0.12,
            "border-color": "rgba(148, 163, 184, 0.28)",
          },
        },
        {
          selector: "edge.muted",
          style: {
            opacity: 0.08,
            "line-color": "rgba(148, 163, 184, 0.22)",
            "target-arrow-color": "rgba(148, 163, 184, 0.18)",
          },
        },
        {
          selector: ":selected",
          style: {
            "border-color": "#f8fafc",
            "border-width": 2.6,
          },
        },
      ],
    });

    this.cy.on("select unselect", () => {
      this.applySelectionFocus();
      this.syncSecondaryLabels();
      this.emitSelection();
    });
    this.cy.on("tap", "node", (event: EventObjectNode) => {
      this.activeNodeId = String(event.target.id());
    });
    this.cy.on("dbltap", "node", (event: EventObjectNode) => {
      const nodeId = String(event.target.id());
      this.activeNodeId = nodeId;
      this.onNodeDoubleClick?.(nodeId);
    });
    this.cy.on("zoom", () => this.syncSecondaryLabels());
  }

  setData(nodes: GraphNode[], edges: GraphEdge[]): void {
    this.fullNodes = new Map(nodes.map((node) => [node.id, node]));
    this.fullEdges = new Map(edges.map((edge) => [edge.id, edge]));
    this.renderFromMaps(this.fullNodes, this.fullEdges, { fit: true, preserveViewport: false, randomize: true });
  }

  appendData(nodes: GraphNode[], edges: GraphEdge[]): void {
    nodes.forEach((node) => this.fullNodes.set(node.id, node));
    edges.forEach((edge) => this.fullEdges.set(edge.id, edge));
    const existingNodeIds = new Set(this.cy.nodes().map((node) => String(node.id())));
    const existingEdgeIds = new Set(this.cy.edges().map((edge) => String(edge.id())));
    const addedNodeIds: string[] = [];
    const additions: ElementDefinition[] = [];

    nodes.forEach((node) => {
      if (existingNodeIds.has(node.id)) return;
      additions.push(toElement(node));
      existingNodeIds.add(node.id);
      addedNodeIds.push(node.id);
    });
    edges.forEach((edge) => {
      if (existingEdgeIds.has(edge.id)) return;
      if (!existingNodeIds.has(edge.source) || !existingNodeIds.has(edge.target)) return;
      additions.push(toEdge(edge));
      existingEdgeIds.add(edge.id);
    });

    if (!additions.length) {
      this.emitSelection();
      return;
    }

    this.cy.add(additions);
    this.syncSecondaryLabels();
    this.restoreActiveSelection(false);

    if (!addedNodeIds.length) {
      this.emitSelection();
      return;
    }

    const neighborhood = this.cy.collection();
    addedNodeIds.forEach((id) => {
      const node = this.cy.getElementById(id);
      if (!node.length) return;
      neighborhood.merge(node);
      neighborhood.merge(node.neighborhood());
    });
    if (!neighborhood.length) {
      this.emitSelection();
      return;
    }
    this.runLayout(neighborhood, {
      fit: false,
      preserveViewport: true,
      randomize: false,
      animate: true,
      lockSelectedAnchor: true,
    });
  }

  showMemorySubset(memoryNodeIds: string[]): void {
    if (!memoryNodeIds.length) {
      this.renderFromMaps(this.fullNodes, this.fullEdges, { fit: true, preserveViewport: false, randomize: false });
      return;
    }
    const allow = new Set(memoryNodeIds);
    const subsetNodes = new Map<string, GraphNode>();
    const subsetEdges = new Map<string, GraphEdge>();

    this.fullEdges.forEach((edge) => {
      if (!allow.has(edge.source) && !allow.has(edge.target)) return;
      subsetEdges.set(edge.id, edge);
      const source = this.fullNodes.get(edge.source);
      const target = this.fullNodes.get(edge.target);
      if (source) subsetNodes.set(source.id, source);
      if (target) subsetNodes.set(target.id, target);
    });

    if (!subsetNodes.size) {
      memoryNodeIds.forEach((id) => {
        const memory = this.fullNodes.get(id);
        if (memory) subsetNodes.set(memory.id, memory);
      });
    }

    this.renderFromMaps(subsetNodes, subsetEdges, { fit: true, preserveViewport: false, randomize: false });
  }

  highlightNode(nodeId: string): void {
    if (!nodeId) return;
    const node = this.cy.getElementById(nodeId);
    if (!node || !node.nonempty()) return;
    this.cy.$(":selected").unselect();
    node.select();
    this.activeNodeId = nodeId;
    this.cy.center(node);
  }

  getMemoryRows(): GraphNode[] {
    return [...this.fullNodes.values()]
      .filter((node) => node.kind === "memory")
      .sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        return a.label.localeCompare(b.label);
      });
  }

  fit(): void {
    this.applyFit(undefined, 44);
  }

  resize(): void {
    this.cy.resize();
  }

  setLayoutMode(mode: LayoutMode): void {
    if (this.layoutMode === mode) return;
    this.layoutMode = mode;
    if (!this.cy.elements().length) return;
    this.runLayout(this.cy.elements(), {
      fit: false,
      preserveViewport: true,
      randomize: false,
      animate: true,
      lockSelectedAnchor: false,
    });
  }

  fitSelection(): void {
    const selected = this.cy.$(":selected");
    if (selected.length) {
      this.applyFit(selected, 36);
      return;
    }
    this.fit();
  }

  centerSelection(): void {
    const selected = this.cy.$(":selected");
    if (selected.length) this.cy.center(selected);
  }

  togglePinSelection(): boolean {
    const selected = this.cy.$("node:selected");
    if (!selected.length) return false;
    const first = selected.first();
    const pinned = Boolean(first.data("pinned"));
    if (pinned) {
      selected.unlock();
      selected.data("pinned", false);
      selected.removeClass("pinned");
    } else {
      selected.lock();
      selected.data("pinned", true);
      selected.addClass("pinned");
    }
    return !pinned;
  }

  collapseNeighborhood(): void {
    const selected = this.cy.$("node:selected").first();
    if (!selected.length) return;
    const neighbors = selected.neighborhood("node");
    const removable = neighbors.filter((node) => {
      if (node.id() === selected.id()) return false;
      if (node.data("kind") === "memory") return false;
      return Boolean(node.data("expanded")) || node.data("kind") !== "memory";
    });
    removable.connectedEdges().remove();
    removable.remove();
    if (!this.cy.elements().length) {
      this.emitSelection();
      return;
    }
    this.runLayout(this.cy.elements(), {
      fit: false,
      preserveViewport: true,
      randomize: false,
      animate: true,
      lockSelectedAnchor: true,
    });
  }

  searchVisible(query: string): string | null {
    const text = query.trim().toLowerCase();
    this.cy.nodes().removeClass("search-hit");
    if (!text) return null;
    const matches = this.cy.nodes().filter((node) => {
      const label = String(node.data("fullLabel") || node.data("label") || "").toLowerCase();
      const kind = String(node.data("kind") || "").toLowerCase();
      return label.includes(text) || kind.includes(text);
    });
    matches.addClass("search-hit");
    const first = matches.first();
    if (first.length) {
      first.select();
      this.cy.center(first);
      return String(first.id());
    }
    return null;
  }

  selectedNodeId(): string | null {
    const selected = this.cy.$("node:selected").first();
    if (selected.length) return String(selected.id());
    return null;
  }

  selectedNode(): GraphNode | null {
    const selectedId = this.selectedNodeId();
    if (!selectedId) return null;
    return this.fullNodes.get(selectedId) || null;
  }

  destroy(): void {
    this.cy.destroy();
  }

  markExpandedNodeIds(nodeIds: string[]): void {
    nodeIds.forEach((id) => {
      const node = this.cy.getElementById(id);
      if (!node || !node.nonempty()) return;
      node.data("expanded", true);
    });
  }

  private renderFromMaps(
    nodes: Map<string, GraphNode>,
    edges: Map<string, GraphEdge>,
    options: { fit: boolean; preserveViewport: boolean; randomize: boolean },
  ): void {
    const elements: ElementDefinition[] = [];
    nodes.forEach((node) => elements.push(toElement(node)));
    edges.forEach((edge) => {
      if (!nodes.has(edge.source) || !nodes.has(edge.target)) return;
      elements.push(toEdge(edge));
    });
    this.cy.elements().remove();
    if (!elements.length) {
      this.emitSelection();
      return;
    }
    this.cy.add(elements);
    this.syncSecondaryLabels();
    this.restoreActiveSelection(false);
    this.runLayout(this.cy.elements(), {
      fit: options.fit,
      preserveViewport: options.preserveViewport,
      randomize: options.randomize,
      animate: true,
      lockSelectedAnchor: options.preserveViewport,
    });
  }

  private runLayout(
    collection: CollectionReturnValue,
    options: {
      fit: boolean;
      preserveViewport: boolean;
      randomize: boolean;
      animate: boolean;
      lockSelectedAnchor: boolean;
    },
  ): void {
    const viewport = options.preserveViewport ? captureViewport(this.cy) : null;
    const selected = this.cy.$("node:selected").first();
    const shouldUnlock = options.lockSelectedAnchor && selected.length > 0 && !selected.locked();
    if (shouldUnlock) selected.lock();

    const layout = collection.layout(this.layoutOptions(options));
    layout.on("layoutstop", () => {
      if (shouldUnlock && selected.length) selected.unlock();
      if (viewport) {
        this.cy.zoom(viewport.zoom);
        this.cy.pan(viewport.pan);
      } else if (options.fit) {
        this.applyFit(undefined, 44);
      }
      this.syncSecondaryLabels();
      this.emitSelection();
    });
    layout.run();
  }

  private layoutOptions(options: { fit: boolean; randomize: boolean; animate: boolean }): Record<string, unknown> {
    if (this.layoutMode === "layered") {
      return {
        name: "elk",
        fit: options.fit,
        animate: options.animate,
        animationDuration: options.animate ? 260 : 0,
        nodeDimensionsIncludeLabels: true,
        elk: {
          algorithm: "layered",
          "elk.direction": "RIGHT",
          "elk.spacing.nodeNode": 28,
          "elk.layered.spacing.nodeNodeBetweenLayers": 56,
          "elk.edgeRouting": "POLYLINE",
        },
      };
    }
    return {
      name: "fcose",
      fit: options.fit,
      padding: options.fit ? 84 : 30,
      animate: options.animate,
      animationDuration: options.animate ? 340 : 0,
      randomize: options.randomize,
      quality: "default",
      packComponents: true,
      nodeSeparation: 120,
      tile: true,
      tilingPaddingVertical: 42,
      tilingPaddingHorizontal: 42,
      sampleSize: 50,
      samplingType: true,
      piTol: 0.00001,
      nodeRepulsion: (node: { data: (key: string) => unknown }) => {
        const kind = String(node.data("kind") || "");
        if (kind === "memory") return 200000;
        if (kind === "session" || kind === "project") return 160000;
        return 130000;
      },
      idealEdgeLength: (edge: { data: (key: string) => unknown }) => {
        const kind = String(edge.data("kind") || "");
        if (kind === "tagged") return 220;
        if (kind === "typed_as") return 210;
        if (kind === "in_project" || kind === "from_session") return 195;
        return 182;
      },
      edgeElasticity: (edge: { data: (key: string) => unknown }) => {
        return 0.16;
      },
      nestingFactor: 0.82,
      gravity: 0.12,
      gravityRange: 3.8,
      initialEnergyOnIncremental: 0.45,
      numIter: 3600,
      componentSpacing: 280,
      nodeOverlap: 0,
      nodeDimensionsIncludeLabels: true,
    };
  }

  private applyFit(target: CollectionReturnValue | undefined, padding: number): void {
    this.cy.fit(target, padding);
    // Keep a stable overview after fit so labels do not collide in tiny datasets.
    const maxOverviewZoom = 0.96;
    if (this.cy.zoom() > maxOverviewZoom) {
      this.cy.zoom(maxOverviewZoom);
      if (target && target.length) {
        this.cy.center(target);
      } else {
        this.cy.center();
      }
    }
  }

  private restoreActiveSelection(center: boolean): void {
    if (!this.activeNodeId) return;
    const node = this.cy.getElementById(this.activeNodeId);
    if (!node || !node.nonempty()) return;
    this.cy.$(":selected").unselect();
    node.select();
    if (center) this.cy.center(node);
  }

  private applySelectionFocus(): void {
    const selectedNode = this.cy.$("node:selected").first();
    const selectedEdge = this.cy.$("edge:selected").first();
    this.cy.nodes().removeClass("muted");
    this.cy.edges().removeClass("muted");

    if (selectedNode.length) {
      const keepNodes = selectedNode.union(selectedNode.neighborhood("node"));
      const keepEdges = selectedNode.connectedEdges();
      this.cy.nodes().not(keepNodes).addClass("muted");
      this.cy.edges().not(keepEdges).addClass("muted");
      return;
    }

    if (selectedEdge.length) {
      const keepNodes = selectedEdge.connectedNodes();
      this.cy.nodes().not(keepNodes).addClass("muted");
      this.cy.edges().not(selectedEdge).addClass("muted");
    }
  }

  private syncSecondaryLabels(): void {
    const zoom = this.cy.zoom();
    this.cy.nodes().forEach((node) => {
      const kind = String(node.data("kind"));
      const selected = node.selected();
      const pinned = Boolean(node.data("pinned"));
      const searchHit = node.hasClass("search-hit");
      if (kind === "memory") {
        const showMemoryLabel = selected || pinned || searchHit || zoom >= 1.48;
        node.data("label", showMemoryLabel ? node.data("fullLabel") : "");
        return;
      }
      if (selected || zoom >= 1.18) {
        node.data("label", node.data("fullLabel"));
      } else {
        node.data("label", "");
      }
    });
  }

  private emitSelection(): void {
    const selectedNode = this.cy.$("node:selected").first();
    const selectedEdge = this.cy.$("edge:selected").first();
    const selectedNodeData = selectedNode.length
      ? this.fullNodes.get(String(selectedNode.id())) || null
      : null;
    const selectedEdgeData = selectedEdge.length
      ? this.fullEdges.get(String(selectedEdge.id())) || null
      : null;
    const connectedNodes: GraphNode[] = [];
    const outgoing = new Map<string, number>();
    const incoming = new Map<string, number>();

    if (selectedNode.length) {
      selectedNode.connectedEdges().forEach((edge) => {
        const source = String(edge.data("source"));
        const target = String(edge.data("target"));
        const kind = String(edge.data("kind"));
        if (source === selectedNode.id()) {
          outgoing.set(kind, (outgoing.get(kind) || 0) + 1);
        } else if (target === selectedNode.id()) {
          incoming.set(kind, (incoming.get(kind) || 0) + 1);
        }
      });
      selectedNode.connectedNodes().forEach((node) => {
        const model = this.fullNodes.get(String(node.id()));
        if (!model) return;
        if (model.id === selectedNode.id()) return;
        connectedNodes.push(model);
      });
      connectedNodes.sort((a, b) => b.score - a.score);
    }

    this.onSelectionChanged({
      selectedNode: selectedNodeData,
      selectedEdge: selectedEdgeData,
      connectedNodes: connectedNodes.slice(0, 12),
      outgoing: [...outgoing.entries()].map(([kind, count]) => ({ kind, count: round(count) })),
      incoming: [...incoming.entries()].map(([kind, count]) => ({ kind, count: round(count) })),
    });
  }
}
