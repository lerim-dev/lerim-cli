import type { GraphNode } from "../state";

export interface TableRenderOptions {
  rows: GraphNode[];
  selectedNodeIds: Set<string>;
}

export interface TableRendererCallbacks {
  onRowClick: (nodeId: string) => void;
  onSelectionChange: (nodeIds: string[]) => void;
}

function valueAsText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (value === null || value === undefined) return "";
  return String(value);
}

function truncate(value: string, limit = 52): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit - 1)}...`;
}

export class TableRenderer {
  private readonly root: HTMLElement;

  private readonly callbacks: TableRendererCallbacks;

  private rows: GraphNode[] = [];

  private selectedNodeIds = new Set<string>();

  constructor(root: HTMLElement, callbacks: TableRendererCallbacks) {
    this.root = root;
    this.callbacks = callbacks;
  }

  render(options: TableRenderOptions): void {
    this.rows = options.rows.slice();
    this.selectedNodeIds = new Set(options.selectedNodeIds);
    this.root.innerHTML = "";

    if (!this.rows.length) {
      this.root.innerHTML = `<div class="agx-empty">No rows to display.</div>`;
      return;
    }

    const table = document.createElement("table");
    table.className = "agx-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th class="agx-col-check">
            <input type="checkbox" data-role="select-all" />
          </th>
          <th>Memory</th>
          <th>Type</th>
          <th>State</th>
          <th>Score</th>
          <th>Tags</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    for (const row of this.rows) {
      const tr = document.createElement("tr");
      tr.dataset.nodeId = row.id;
      if (this.selectedNodeIds.has(row.id)) tr.classList.add("is-selected");
      const tags = valueAsText(row.properties?.tags ?? []);
      const type = valueAsText(row.properties?.kind ?? "");
      const state = valueAsText(row.properties?.kind ?? "");
      tr.innerHTML = `
        <td class="agx-col-check">
          <input type="checkbox" data-role="select-row" ${this.selectedNodeIds.has(row.id) ? "checked" : ""} />
        </td>
        <td title="${row.label}">${truncate(row.label)}</td>
        <td>${type || "-"}</td>
        <td>${state || "-"}</td>
        <td>${row.score.toFixed(3)}</td>
        <td title="${tags}">${truncate(tags || "-", 36)}</td>
      `;
      tbody.appendChild(tr);
    }

    this.root.appendChild(table);
    this.bindEvents(table);
    this.syncSelectAll(table);
  }

  private bindEvents(table: HTMLTableElement): void {
    table.addEventListener("click", (event) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      const rowEl = target.closest("tr");
      if (!rowEl || !rowEl.dataset.nodeId) return;

      if ((target as HTMLInputElement).dataset.role === "select-row") {
        const checkbox = target as HTMLInputElement;
        this.toggleSelection(rowEl.dataset.nodeId, checkbox.checked);
        rowEl.classList.toggle("is-selected", checkbox.checked);
        this.syncSelectAll(table);
        this.callbacks.onSelectionChange([...this.selectedNodeIds]);
        return;
      }

      this.callbacks.onRowClick(rowEl.dataset.nodeId);
    });

    const selectAll = table.querySelector<HTMLInputElement>('[data-role="select-all"]');
    if (!selectAll) return;
    selectAll.addEventListener("change", () => {
      const checked = Boolean(selectAll.checked);
      const rowChecks = table.querySelectorAll<HTMLInputElement>('[data-role="select-row"]');
      rowChecks.forEach((input) => {
        input.checked = checked;
        const rowEl = input.closest("tr");
        if (rowEl?.dataset.nodeId) {
          this.toggleSelection(rowEl.dataset.nodeId, checked);
          rowEl.classList.toggle("is-selected", checked);
        }
      });
      this.callbacks.onSelectionChange([...this.selectedNodeIds]);
    });
  }

  private toggleSelection(nodeId: string, selected: boolean): void {
    if (selected) this.selectedNodeIds.add(nodeId);
    else this.selectedNodeIds.delete(nodeId);
  }

  private syncSelectAll(table: HTMLTableElement): void {
    const selectAll = table.querySelector<HTMLInputElement>('[data-role="select-all"]');
    if (!selectAll) return;
    const rowChecks = [...table.querySelectorAll<HTMLInputElement>('[data-role="select-row"]')];
    if (!rowChecks.length) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
      return;
    }
    const checkedCount = rowChecks.filter((item) => item.checked).length;
    selectAll.checked = checkedCount === rowChecks.length;
    selectAll.indeterminate = checkedCount > 0 && checkedCount < rowChecks.length;
  }
}
