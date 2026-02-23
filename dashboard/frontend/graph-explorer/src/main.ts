import "./style.css";

import { expandGraph, fetchGraphOptions, queryGraph } from "./api";
import { GraphRenderer, type SelectionSnapshot } from "./render/graph";
import { InspectorRenderer } from "./render/inspector";
import { TableRenderer } from "./render/table";
import { EMPTY_STATS, createInitialState, type GraphNode, type GraphOptionsResponse, type GraphQueryRequest } from "./state";

type NoticeLevel = "info" | "error" | "success";

export interface GraphExplorerMountOptions {
  onOpenMemory?: (memoryId: string) => void;
  onNotify?: (message: string, level?: NoticeLevel) => void;
  initialQuery?: string;
}

export interface GraphExplorerApp {
  runQuery: () => Promise<void>;
  refreshOptions: () => Promise<void>;
  destroy: () => void;
  setQuery: (query: string) => void;
  resize: () => void;
}

interface ExplorerElements {
  shell: HTMLElement;
  typeChecklist: HTMLElement;
  stateChecklist: HTMLElement;
  projectChecklist: HTMLElement;
  tagChecklist: HTMLElement;
  queryInput: HTMLInputElement;
  maxNodesInput: HTMLInputElement;
  maxEdgesInput: HTMLInputElement;
  runQueryButton: HTMLButtonElement;
  resetFiltersButton: HTMLButtonElement;
  loadingMask: HTMLElement;
  warnings: HTMLElement;
  summary: HTMLElement;
  visibleSearchInput: HTMLInputElement;
  visibleSearchButton: HTMLButtonElement;
  viewButtons: HTMLButtonElement[];
  graphPane: HTMLElement;
  tablePane: HTMLElement;
  fitButton: HTMLButtonElement;
  focusButton: HTMLButtonElement;
  toggleLeftButton: HTMLButtonElement;
  toggleRightButton: HTMLButtonElement;
  layoutButtons: HTMLButtonElement[];
  subsetLabel: HTMLElement;
}

const DEFAULT_MAX_NODES = 200;
const DEFAULT_MAX_EDGES = 3000;
const DEFAULT_EXPAND_NODES = 500;
const DEFAULT_EXPAND_EDGES = 1200;

function hasActiveFilters(payload: GraphQueryRequest): boolean {
  return (
    payload.filters.type.length > 0 ||
    payload.filters.state.length > 0 ||
    payload.filters.projects.length > 0 ||
    payload.filters.tags.length > 0
  );
}

function checkedValues(container: HTMLElement): string[] {
  return [...container.querySelectorAll<HTMLInputElement>("input[type='checkbox'][data-filter-value]")]
    .filter((item) => item.checked)
    .map((item) => item.value)
    .filter(Boolean);
}

function checkboxId(filterName: string, value: string, index: number): string {
  const slug = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  return `agx-${filterName}-${slug || "item"}-${index}`;
}

function populateChecklist(container: HTMLElement, values: string[]): void {
  const selected = new Set(checkedValues(container));
  const filterName = container.dataset.filterName || "filter";
  container.innerHTML = "";
  if (!values.length) {
    const empty = document.createElement("div");
    empty.className = "agx-muted";
    empty.textContent = "No options";
    container.appendChild(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  values.forEach((value, index) => {
    const id = checkboxId(filterName, value, index);
    const label = document.createElement("label");
    label.className = "agx-check-item";
    label.setAttribute("for", id);

    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id;
    input.value = value;
    input.dataset.filterValue = value;
    input.checked = selected.has(value);

    const text = document.createElement("span");
    text.textContent = value;

    label.append(input, text);
    fragment.appendChild(label);
  });
  container.appendChild(fragment);
}

function resetChecklist(container: HTMLElement): void {
  for (const item of container.querySelectorAll<HTMLInputElement>("input[type='checkbox'][data-filter-value]")) {
    item.checked = false;
  }
}

function dedupeNodes(items: GraphNode[]): GraphNode[] {
  const byId = new Map<string, GraphNode>();
  for (const item of items) byId.set(item.id, item);
  return [...byId.values()];
}

function dedupeEdges<T extends { id: string }>(items: T[]): T[] {
  const byId = new Map<string, T>();
  for (const item of items) byId.set(item.id, item);
  return [...byId.values()];
}

function shellMarkup(): string {
  return `
    <div class="agx-shell" data-el="shell" tabindex="0">
      <section class="agx-pane agx-pane-left" data-el="left-pane">
        <h3>Query and filters</h3>
        <div class="agx-legend agx-legend-top" aria-label="Node type legend">
          <div class="agx-legend-item"><span class="agx-dot memory"></span>Memory</div>
          <div class="agx-legend-item"><span class="agx-dot session"></span>Session</div>
          <div class="agx-legend-item"><span class="agx-dot project"></span>Project</div>
          <div class="agx-legend-item"><span class="agx-dot tag"></span>Tag</div>
          <div class="agx-legend-item"><span class="agx-dot type"></span>Type</div>
        </div>
        <div class="agx-field">
          <label>Search query</label>
          <input data-el="query" type="text" placeholder="search memories, tags, sessions..." />
        </div>
        <div class="agx-field">
          <label>Types</label>
          <div class="agx-checklist" data-filter-name="types" data-el="types"></div>
        </div>
        <div class="agx-field">
          <label>States</label>
          <div class="agx-checklist" data-filter-name="states" data-el="states"></div>
        </div>
        <div class="agx-field">
          <label>Projects</label>
          <div class="agx-checklist" data-filter-name="projects" data-el="projects"></div>
        </div>
        <div class="agx-field">
          <label>Tags</label>
          <div class="agx-checklist" data-filter-name="tags" data-el="tags"></div>
        </div>
        <div class="agx-field">
          <label>Max nodes</label>
          <input data-el="max-nodes" type="number" min="100" max="5000" step="100" value="${DEFAULT_MAX_NODES}" />
        </div>
        <div class="agx-field">
          <label>Max edges</label>
          <input data-el="max-edges" type="number" min="500" max="12000" step="100" value="${DEFAULT_MAX_EDGES}" />
        </div>
        <div class="agx-btn-row">
          <button data-el="run-query">Visualize selection</button>
          <button class="secondary" data-el="reset-filters">Reset</button>
        </div>
        <div data-el="summary" class="agx-summary">Run a query to load a focused subgraph.</div>
        <div data-el="subset-label" class="agx-summary"></div>
        <div data-el="warnings" class="agx-warning-list"></div>
      </section>

      <section class="agx-pane agx-center">
        <div class="agx-toolbar">
          <div class="agx-tabs">
            <button data-view="graph" class="is-active">Graph</button>
            <button data-view="table">Table</button>
          </div>
          <div class="agx-toolbar-actions">
            <div class="agx-tabs agx-layout-tabs">
              <button data-layout="force" class="is-active">Force</button>
              <button data-layout="layered">Layered</button>
            </div>
            <button data-el="toggle-left" class="agx-ghost">Hide filters</button>
            <button data-el="toggle-right" class="agx-ghost">Hide inspector</button>
            <button data-el="fit">Fit</button>
            <button data-el="focus">Fit selection</button>
          </div>
        </div>
        <div class="agx-search-row">
          <input data-el="visible-search" type="text" placeholder="Search visible graph (/)" />
          <button data-el="visible-search-button">Find</button>
        </div>
        <div class="agx-canvas-wrap" data-el="graph-pane">
          <div data-el="canvas" class="agx-canvas"></div>
          <div data-el="loading" class="agx-loading" hidden>Loading graph...</div>
        </div>
        <div class="agx-table-wrap" data-el="table-pane" style="display:none"></div>
      </section>

      <section class="agx-pane agx-pane-right" data-el="right-pane">
        <div data-el="inspector"></div>
      </section>
    </div>
  `;
}

class GraphExplorerController implements GraphExplorerApp {
  private readonly root: HTMLElement;

  private readonly options: GraphExplorerMountOptions;

  private readonly state = createInitialState();

  private readonly elements: ExplorerElements;

  private readonly graph: GraphRenderer;

  private readonly table: TableRenderer;

  private readonly inspector: InspectorRenderer;

  private selectedMemoryNodeIds = new Set<string>();

  private leftCollapsed = false;

  private rightCollapsed = false;

  private keydownHandler: (event: KeyboardEvent) => void;

  private resizeTimeoutId: number | null = null;

  constructor(root: HTMLElement, options: GraphExplorerMountOptions) {
    this.root = root;
    this.options = options;
    this.root.innerHTML = shellMarkup();
    this.elements = this.resolveElements();
    this.graph = new GraphRenderer(
      this.query("[data-el='canvas']"),
      (snapshot) => this.onSelectionChanged(snapshot),
      (nodeId) => {
        void this.expandOneHop(nodeId);
      },
    );
    this.table = new TableRenderer(this.elements.tablePane, {
      onRowClick: (nodeId) => this.onTableRowClick(nodeId),
      onSelectionChange: (nodeIds) => this.onTableSelectionChanged(nodeIds),
    });
    this.inspector = new InspectorRenderer(this.query("[data-el='inspector']"), {
      onCenter: () => this.graph.centerSelection(),
      onFit: () => this.graph.fitSelection(),
      onPin: () => this.graph.togglePinSelection(),
      onCollapse: () => this.graph.collapseNeighborhood(),
      onOpenMemory: () => this.openSelectedMemory(),
    });

    this.keydownHandler = (event) => this.handleKeydown(event);
    this.bindEvents();

    if (options.initialQuery) this.setQuery(options.initialQuery);
    void this.refreshOptions();
  }

  async runQuery(): Promise<void> {
    this.setLoading(true);
    try {
      const payload = this.buildQueryPayload();
      let response = await queryGraph(payload);
      let broadened = false;

      if (!response.stats?.returned_nodes) {
        const relaxedPayload: GraphQueryRequest = {
          ...payload,
          filters: {
            type: [],
            state: [],
            projects: [],
            tags: [],
          },
        };

        if (hasActiveFilters(payload)) {
          const relaxed = await queryGraph(relaxedPayload);
          if (relaxed.stats?.returned_nodes) {
            response = {
              ...relaxed,
              warnings: [
                ...(relaxed.warnings || []),
                "No results for the selected filter subset. Showing a broader graph.",
              ],
            };
            broadened = true;
          } else if (payload.query) {
            const broadTop = await queryGraph({ ...relaxedPayload, query: "" });
            if (broadTop.stats?.returned_nodes) {
              response = {
                ...broadTop,
                warnings: [
                  ...(broadTop.warnings || []),
                  "No results for selected filters and query. Showing top memories instead.",
                ],
              };
              broadened = true;
            }
          }
        } else if (payload.query) {
          const broadTop = await queryGraph({ ...payload, query: "" });
          if (broadTop.stats?.returned_nodes) {
            response = {
              ...broadTop,
              warnings: [
                ...(broadTop.warnings || []),
                "No results for this query. Showing top memories instead.",
              ],
            };
            broadened = true;
          }
        }
      }

      this.state.nodes = dedupeNodes(response.nodes);
      this.state.edges = dedupeEdges(response.edges);
      this.state.stats = response.stats || { ...EMPTY_STATS };
      this.state.warnings = response.warnings || [];
      this.selectedMemoryNodeIds.clear();

      this.graph.setData(this.state.nodes, this.state.edges);
      this.renderTable();
      this.renderWarnings();
      this.renderSummary();
      this.renderSubsetLabel();
      if (broadened) {
        this.notify("Filters were too narrow, so I broadened the graph automatically.", "info");
      } else {
        this.notify("Graph rendered.", "success");
      }
    } catch (error) {
      this.notify(error instanceof Error ? error.message : "Failed to query graph.", "error");
    } finally {
      this.setLoading(false);
    }
  }

  async refreshOptions(): Promise<void> {
    try {
      const options = await fetchGraphOptions();
      this.applyOptions(options);
    } catch (error) {
      this.notify(error instanceof Error ? error.message : "Failed to load graph options.", "error");
    }
  }

  setQuery(query: string): void {
    this.elements.queryInput.value = query || "";
  }

  resize(): void {
    this.graph.resize();
  }

  destroy(): void {
    window.removeEventListener("keydown", this.keydownHandler);
    if (this.resizeTimeoutId !== null) {
      window.clearTimeout(this.resizeTimeoutId);
      this.resizeTimeoutId = null;
    }
    this.graph.destroy();
    this.root.innerHTML = "";
  }

  private resolveElements(): ExplorerElements {
    return {
      shell: this.query("[data-el='shell']"),
      typeChecklist: this.query("[data-el='types']"),
      stateChecklist: this.query("[data-el='states']"),
      projectChecklist: this.query("[data-el='projects']"),
      tagChecklist: this.query("[data-el='tags']"),
      queryInput: this.query("[data-el='query']"),
      maxNodesInput: this.query("[data-el='max-nodes']"),
      maxEdgesInput: this.query("[data-el='max-edges']"),
      runQueryButton: this.query("[data-el='run-query']"),
      resetFiltersButton: this.query("[data-el='reset-filters']"),
      loadingMask: this.query("[data-el='loading']"),
      warnings: this.query("[data-el='warnings']"),
      summary: this.query("[data-el='summary']"),
      visibleSearchInput: this.query("[data-el='visible-search']"),
      visibleSearchButton: this.query("[data-el='visible-search-button']"),
      viewButtons: [...this.root.querySelectorAll<HTMLButtonElement>("[data-view]")],
      graphPane: this.query("[data-el='graph-pane']"),
      tablePane: this.query("[data-el='table-pane']"),
      fitButton: this.query("[data-el='fit']"),
      focusButton: this.query("[data-el='focus']"),
      toggleLeftButton: this.query("[data-el='toggle-left']"),
      toggleRightButton: this.query("[data-el='toggle-right']"),
      layoutButtons: [...this.root.querySelectorAll<HTMLButtonElement>("[data-layout]")],
      subsetLabel: this.query("[data-el='subset-label']"),
    };
  }

  private query<T extends HTMLElement = HTMLElement>(selector: string): T {
    const node = this.root.querySelector(selector);
    if (!node) throw new Error(`Missing explorer node: ${selector}`);
    return node as T;
  }

  private bindEvents(): void {
    this.elements.runQueryButton.addEventListener("click", () => {
      void this.runQuery();
    });
    this.elements.resetFiltersButton.addEventListener("click", () => this.resetFilters());
    this.elements.visibleSearchButton.addEventListener("click", () => this.searchVisibleGraph());
    this.elements.visibleSearchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      this.searchVisibleGraph();
    });
    this.elements.fitButton.addEventListener("click", () => this.graph.fit());
    this.elements.focusButton.addEventListener("click", () => this.graph.fitSelection());
    this.elements.toggleLeftButton.addEventListener("click", () => this.toggleSidebar("left"));
    this.elements.toggleRightButton.addEventListener("click", () => this.toggleSidebar("right"));
    this.elements.viewButtons.forEach((button) => {
      button.addEventListener("click", () => this.setView(button.dataset.view === "table" ? "table" : "graph"));
    });
    this.elements.layoutButtons.forEach((button) => {
      button.addEventListener("click", () => this.setLayout(button.dataset.layout === "layered" ? "layered" : "force"));
    });
    window.addEventListener("keydown", this.keydownHandler);
  }

  private buildQueryPayload(): GraphQueryRequest {
    const maxNodes = Math.max(100, Math.min(5000, Number(this.elements.maxNodesInput.value || DEFAULT_MAX_NODES)));
    const maxEdges = Math.max(500, Math.min(12000, Number(this.elements.maxEdgesInput.value || DEFAULT_MAX_EDGES)));
    return {
      query: this.elements.queryInput.value.trim(),
      filters: {
        type: checkedValues(this.elements.typeChecklist),
        state: checkedValues(this.elements.stateChecklist),
        projects: checkedValues(this.elements.projectChecklist),
        tags: checkedValues(this.elements.tagChecklist),
      },
      limits: {
        max_nodes: maxNodes,
        max_edges: maxEdges,
      },
      seed_ids: [],
      view: "graph",
    };
  }

  private applyOptions(options: GraphOptionsResponse): void {
    populateChecklist(this.elements.typeChecklist, options.types || []);
    populateChecklist(this.elements.stateChecklist, options.states || []);
    populateChecklist(this.elements.projectChecklist, options.projects || []);
    populateChecklist(this.elements.tagChecklist, options.tags || []);
  }

  private setLoading(loading: boolean): void {
    this.state.loading = loading;
    this.elements.loadingMask.hidden = !loading;
    this.elements.runQueryButton.disabled = loading;
  }

  private renderWarnings(): void {
    this.elements.warnings.innerHTML = "";
    if (!this.state.warnings.length) return;
    for (const warning of this.state.warnings) {
      const item = document.createElement("div");
      item.className = "agx-warning";
      item.textContent = warning;
      this.elements.warnings.appendChild(item);
    }
  }

  private renderSummary(): void {
    const stats = this.state.stats || EMPTY_STATS;
    if (!stats.returned_nodes && !stats.returned_edges) {
      this.elements.summary.textContent = "No graph data returned for this query.";
      return;
    }
    this.elements.summary.textContent = `Matched ${stats.matched_memories} memories. Showing ${stats.returned_nodes} nodes and ${stats.returned_edges} edges${stats.truncated ? " (truncated)" : ""}.`;
  }

  private renderSubsetLabel(): void {
    if (!this.selectedMemoryNodeIds.size) {
      this.elements.subsetLabel.textContent = "";
      return;
    }
    this.elements.subsetLabel.textContent = `Visualizing subset of ${this.selectedMemoryNodeIds.size} selected memories.`;
  }

  private renderTable(): void {
    const rows = this.graph.getMemoryRows();
    this.table.render({ rows, selectedNodeIds: this.selectedMemoryNodeIds });
  }

  private onTableRowClick(nodeId: string): void {
    this.graph.highlightNode(nodeId);
    this.setView("graph");
  }

  private onTableSelectionChanged(nodeIds: string[]): void {
    this.selectedMemoryNodeIds = new Set(nodeIds);
    this.renderSubsetLabel();
    if (!nodeIds.length) {
      this.graph.showMemorySubset([]);
      return;
    }
    this.graph.showMemorySubset(nodeIds);
  }

  private onSelectionChanged(snapshot: SelectionSnapshot): void {
    this.state.selectedNodeId = snapshot.selectedNode?.id || null;
    this.state.selectedEdgeId = snapshot.selectedEdge?.id || null;
    this.inspector.setSnapshot(snapshot);
  }

  private setView(view: "graph" | "table"): void {
    this.state.viewMode = view;
    this.elements.viewButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === view);
    });
    this.elements.graphPane.style.display = view === "graph" ? "block" : "none";
    this.elements.tablePane.style.display = view === "table" ? "block" : "none";
    if (view === "graph") {
      requestAnimationFrame(() => this.graph.resize());
    }
  }

  private searchVisibleGraph(): void {
    const query = this.elements.visibleSearchInput.value.trim();
    const nodeId = this.graph.searchVisible(query);
    if (query && !nodeId) this.notify("No visible node matched search.", "info");
  }

  private async expandOneHop(nodeId?: string): Promise<void> {
    if (this.state.loading) return;
    const selectedNodeId = nodeId || this.graph.selectedNodeId();
    if (!selectedNodeId) {
      this.notify("Select a node first.", "info");
      return;
    }
    this.setLoading(true);
    try {
      const response = await expandGraph({
        node_id: selectedNodeId,
        depth: 1,
        limits: {
          max_nodes: DEFAULT_EXPAND_NODES,
          max_edges: DEFAULT_EXPAND_EDGES,
        },
      });
      this.state.nodes = dedupeNodes([...this.state.nodes, ...(response.nodes || [])]);
      this.state.edges = dedupeEdges([...this.state.edges, ...(response.edges || [])]);
      this.graph.appendData(response.nodes || [], response.edges || []);
      this.graph.markExpandedNodeIds((response.nodes || []).map((item) => item.id));
      this.state.warnings = [...new Set([...(this.state.warnings || []), ...(response.warnings || [])])];
      this.renderWarnings();
      this.renderTable();

      if (this.selectedMemoryNodeIds.size) {
        this.graph.showMemorySubset([...this.selectedMemoryNodeIds]);
      }
      if (response.stats?.truncated) this.notify("Expansion was truncated for performance.", "info");
    } catch (error) {
      this.notify(error instanceof Error ? error.message : "Failed to expand graph.", "error");
    } finally {
      this.setLoading(false);
    }
  }

  private openSelectedMemory(): void {
    const node = this.graph.selectedNode();
    if (!node || node.kind !== "memory") return;
    const memoryId = node.properties?.memory_id;
    if (typeof memoryId !== "string" || !memoryId) return;
    this.options.onOpenMemory?.(memoryId);
  }

  private resetFilters(): void {
    this.elements.queryInput.value = "";
    this.elements.maxNodesInput.value = String(DEFAULT_MAX_NODES);
    this.elements.maxEdgesInput.value = String(DEFAULT_MAX_EDGES);
    [this.elements.typeChecklist, this.elements.stateChecklist, this.elements.projectChecklist, this.elements.tagChecklist].forEach(
      (checklist) => resetChecklist(checklist),
    );
    this.state.nodes = [];
    this.state.edges = [];
    this.state.stats = { ...EMPTY_STATS };
    this.state.warnings = [];
    this.selectedMemoryNodeIds.clear();
    this.graph.setData([], []);
    this.renderTable();
    this.renderWarnings();
    this.renderSummary();
    this.renderSubsetLabel();
  }

  private handleKeydown(event: KeyboardEvent): void {
    if (this.root.offsetParent === null) return;
    const target = event.target as HTMLElement | null;
    const inEditable =
      !!target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable);
    if (event.key === "/" && !inEditable) {
      event.preventDefault();
      this.elements.visibleSearchInput.focus();
      return;
    }
    if (inEditable) return;
    if (event.key.toLowerCase() === "f") {
      event.preventDefault();
      this.graph.fitSelection();
      return;
    }
    if (event.key === "[") {
      event.preventDefault();
      this.toggleSidebar("left");
      return;
    }
    if (event.key === "]") {
      event.preventDefault();
      this.toggleSidebar("right");
    }
  }

  private setLayout(mode: "force" | "layered"): void {
    this.elements.layoutButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.layout === mode);
    });
    this.graph.setLayoutMode(mode);
  }

  private toggleSidebar(side: "left" | "right"): void {
    if (side === "left") {
      this.leftCollapsed = !this.leftCollapsed;
    } else {
      this.rightCollapsed = !this.rightCollapsed;
    }
    this.elements.shell.classList.toggle("agx-left-collapsed", this.leftCollapsed);
    this.elements.shell.classList.toggle("agx-right-collapsed", this.rightCollapsed);
    this.elements.toggleLeftButton.textContent = this.leftCollapsed ? "Show filters" : "Hide filters";
    this.elements.toggleRightButton.textContent = this.rightCollapsed ? "Show inspector" : "Hide inspector";
    this.elements.toggleLeftButton.setAttribute("aria-pressed", String(this.leftCollapsed));
    this.elements.toggleRightButton.setAttribute("aria-pressed", String(this.rightCollapsed));
    requestAnimationFrame(() => this.graph.resize());
    if (this.resizeTimeoutId !== null) {
      window.clearTimeout(this.resizeTimeoutId);
    }
    this.resizeTimeoutId = window.setTimeout(() => {
      this.graph.resize();
      this.resizeTimeoutId = null;
    }, 240);
  }

  private notify(message: string, level: NoticeLevel = "info"): void {
    this.options.onNotify?.(message, level);
  }
}

export function mountGraphExplorer(root: HTMLElement, options: GraphExplorerMountOptions = {}): GraphExplorerApp {
  return new GraphExplorerController(root, options);
}

declare global {
  interface Window {
    LerimGraphExplorer?: {
      mountGraphExplorer: typeof mountGraphExplorer;
    };
  }
}

if (typeof window !== "undefined") {
  window.LerimGraphExplorer = {
    mountGraphExplorer,
  };
}
