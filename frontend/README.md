# Frontend — Vite + React 19

This directory contains the SPA for the Agent Platform Model Telemetry portal.

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Vite dev server (default port 5173). All `/api/*` requests are proxied to `http://127.0.0.1:8000` as configured in `vite.config.js`. |
| `npm run build` | Production build — compiles and copies assets to `../backend/static/`, where FastAPI serves them as static files. |
| `npm run lint` | Run Oxlint on the source tree. |

For backend setup and full project documentation, see the [root README](../README.md).
