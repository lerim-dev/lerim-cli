# Dashboard UI

Unified dashboard frontend lives here.

- `index.html`: main dashboard page (tabs, Alpine stores, UI actions).
- `assets/graph-explorer/`: built graph explorer bundle used by Memories tab.
- `frontend/graph-explorer/`: Vite + TypeScript source for graph explorer bundle.

Served by `src/lerim/app/dashboard.py` from this folder as the static root.

Notes:
- Dashboard is read-only by design for 004 runtime.
- Runtime writes stay CLI-first (`sync`, `maintain`, `memory` commands).
