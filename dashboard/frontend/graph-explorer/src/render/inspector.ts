import type { GraphEdge, GraphNode } from "../state";
import { KIND_COLOR } from "./graph";
import type { SelectionSnapshot } from "./graph";

export interface InspectorActions {
  onCenter: () => void;
  onFit: () => void;
  onPin: () => void;
  onCollapse: () => void;
  onOpenMemory: () => void;
}

function renderRelationRows(rows: Array<{ kind: string; count: number }>): string {
  if (!rows.length) return `<div class="agx-muted">none</div>`;
  return rows
    .map((row) => `<div><span>${escapeHtml(row.kind)}</span><strong>${row.count}</strong></div>`)
    .join("");
}

function nodeMemoryId(node: GraphNode | null): string {
  if (!node) return "";
  const fromProp = node.properties?.memory_id;
  return typeof fromProp === "string" ? fromProp : "";
}

function renderPropValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return value === undefined || value === null ? "" : String(value);
}

function includePropertyKey(key: string): boolean {
  // body_preview is already shown in the dedicated Content block.
  return key !== "body_preview";
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function selectedContent(node: GraphNode | null): string {
  if (!node) return "";
  if (node.kind === "memory") {
    const textCandidates = [
      node.properties?.body,
      node.properties?.body_preview,
      node.properties?.summary,
      node.label,
    ];
    for (const candidate of textCandidates) {
      if (typeof candidate !== "string") continue;
      const trimmed = candidate.trim();
      if (trimmed) return trimmed;
    }
  }
  return node.label || "";
}

export class InspectorRenderer {
  private readonly root: HTMLElement;

  private readonly actions: InspectorActions;

  private snapshot: SelectionSnapshot = {
    selectedNode: null,
    selectedEdge: null,
    connectedNodes: [],
    outgoing: [],
    incoming: [],
  };

  constructor(root: HTMLElement, actions: InspectorActions) {
    this.root = root;
    this.actions = actions;
    this.render();
  }

  setSnapshot(snapshot: SelectionSnapshot): void {
    this.snapshot = snapshot;
    this.render();
  }

  private render(): void {
    const node = this.snapshot.selectedNode;
    const edge = this.snapshot.selectedEdge;
    const memoryId = nodeMemoryId(node);
    const isMemory = node?.kind === "memory" && memoryId;
    const kindColor = node ? KIND_COLOR[node.kind] || "#8ea0b8" : "#8ea0b8";
    const content = selectedContent(node);

    const topConnected = this.snapshot.connectedNodes
      .slice(0, 8)
      .map(
        (item) =>
          `<li title="${escapeHtml(item.label)}"><span class="agx-conn-kind" style="background:${KIND_COLOR[item.kind] || "#8ea0b8"}"></span>${escapeHtml(item.label)}</li>`,
      )
      .join("");

    const props = node
      ? Object.entries(node.properties || {})
          .filter(([key]) => includePropertyKey(key))
          .map(([key, value]) => `<div><span>${escapeHtml(key)}</span><strong>${escapeHtml(renderPropValue(value))}</strong></div>`)
          .join("")
      : "";

    this.root.innerHTML = `
      <div class="agx-inspector-block">
        <h4>Selection</h4>
        ${
          node
            ? `
          <div class="agx-selected-card">
            <div class="agx-selected-kind" style="color:${kindColor}">${escapeHtml(node.kind)}</div>
            <div class="agx-selected-title">${escapeHtml(node.label)}</div>
            <div class="agx-selected-id">${escapeHtml(node.id)}</div>
          </div>
        `
            : edge
              ? `
          <div class="agx-selected-card">
            <div class="agx-selected-kind">edge</div>
            <div class="agx-selected-title">${escapeHtml(edge.kind)}</div>
            <div class="agx-selected-id">${escapeHtml(edge.source)} -> ${escapeHtml(edge.target)}</div>
          </div>
        `
              : `<div class="agx-empty">Select a node or edge.</div>`
        }
      </div>

      <div class="agx-inspector-block">
        <h4>Content</h4>
        ${
          content
            ? `<pre class="agx-content">${escapeHtml(content)}</pre>`
            : '<div class="agx-muted">No content for this node.</div>'
        }
      </div>

      <div class="agx-inspector-block">
        <h4>Actions</h4>
        <div class="agx-muted" style="margin-bottom:8px;">Double-click a node to expand one hop.</div>
        <div class="agx-actions-grid">
          <button data-action="center" ${node ? "" : "disabled"}>Center</button>
          <button data-action="fit">Fit</button>
          <button data-action="pin" ${node ? "" : "disabled"}>Pin / Unpin</button>
          <button data-action="collapse" ${node ? "" : "disabled"}>Collapse</button>
          <button data-action="open-memory" ${isMemory ? "" : "disabled"}>Open memory editor</button>
        </div>
      </div>

      <div class="agx-inspector-block">
        <h4>Connections</h4>
        <div class="agx-kv">
          <div><span>Outgoing</span><strong>${this.snapshot.outgoing.reduce((sum, item) => sum + item.count, 0)}</strong></div>
          <div><span>Incoming</span><strong>${this.snapshot.incoming.reduce((sum, item) => sum + item.count, 0)}</strong></div>
        </div>
        <div class="agx-kv">${renderRelationRows(this.snapshot.outgoing)}</div>
        <div class="agx-kv">${renderRelationRows(this.snapshot.incoming)}</div>
      </div>

      <div class="agx-inspector-block">
        <h4>Top connected</h4>
        ${topConnected ? `<ul class="agx-connected-list">${topConnected}</ul>` : '<div class="agx-muted">none</div>'}
      </div>

      <div class="agx-inspector-block">
        <h4>Other Properties</h4>
        ${props ? `<div class="agx-kv agx-props-grid">${props}</div>` : '<div class="agx-muted">none</div>'}
      </div>
    `;

    this.bindActions();
  }

  private bindActions(): void {
    const bind = (name: string, cb: () => void) => {
      this.root.querySelector(`[data-action="${name}"]`)?.addEventListener("click", cb);
    };
    bind("center", this.actions.onCenter);
    bind("fit", this.actions.onFit);
    bind("pin", this.actions.onPin);
    bind("collapse", this.actions.onCollapse);
    bind("open-memory", this.actions.onOpenMemory);
  }
}
