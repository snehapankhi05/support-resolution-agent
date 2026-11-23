# Support Resolution — frontend

Vite + React + TypeScript SPA for the customer and reviewer portals (Phase 10).

## Dev

Requires the Stage 5 FastAPI process on `127.0.0.1:8000` (database `support_ui`).
The Vite proxy forwards `/auth`, `/orders`, `/tickets`, `/runs`, `/proposals`, `/me`, and `/health` so the browser stays same-origin (no CORS change).

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173/ — the P0 scaffold page checks `GET /health` through the proxy. The dev server binds to `127.0.0.1` (not only IPv6 localhost).

## Scripts

| Command | Purpose |
|---------|---------|
| `npm run dev` | Dev server + API proxy |
| `npm run build` | Typecheck + production bundle |
| `npm run preview` | Serve the production build |

Do not put payment provider URLs or `execute_approved_refund` in this app.
