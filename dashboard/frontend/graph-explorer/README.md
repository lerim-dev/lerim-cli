# graph-explorer

Vite + TypeScript bundle for the Memory Graph Explorer UI.

- `src/main.ts`: explorer app lifecycle and dashboard bridge.
- `src/api.ts`: API client for `/api/memory-graph/*`.
- `src/state.ts`: shared request/response and view types.
- `src/render/graph.ts`: Cytoscape + ELK rendering and interaction helpers.
- `src/render/table.ts`: table view and subset selection.
- `src/render/inspector.ts`: right-side inspector and action wiring.

Build output is written to `dashboard/assets/graph-explorer/` and loaded by `dashboard/index.html`.

Commands:
- `npm run dev` for local explorer development
- `npm run build` to refresh `dashboard/assets/graph-explorer/*`
