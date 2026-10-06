# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

VintedPro — a personal dashboard + automation system for reselling on Vinted across multiple accounts
("dressings"). A FastAPI backend drives Playwright-controlled browser profiles (Chrome/Edge/Brave) that
carry a bundled third-party extension (`backend/clemz_extension`, the "Clemz" tool) to automate listing
creation, republishing, price drops, and buyer messages. A React (Vite) frontend gives a human dashboard
over inventory, sourcing, and automation controls. Data lives in Supabase; Notion is legacy/still used by
some older scraper paths.

## Commands

### Backend (from `backend/`, Python, Windows-oriented)
- Run the API directly: `python main.py` (or `python run.py`, which wraps uvicorn with `reload=False`)
- Install deps: `pip install -r requirements.txt` (encoded as UTF-16 — if editing it, preserve that or
  re-save as UTF-8 only if you confirm pip handles it; check before assuming plain-text edits will apply)
- No test suite exists in this repo (no `pytest` files, only an empty `.pytest_cache`). Verify backend
  changes by running the server and hitting the affected route, or by importing/exercising the specific
  service module directly.
- `dev_mode_on.bat` / `dev_mode_off.bat`: toggle between running the backend manually from a terminal vs.
  as the Windows Scheduled Task `"VintedPro Backend"` (production). Only one can hold port 8000/the browser
  profiles at a time — check which mode is active before starting the server manually.

### Frontend (from `frontend/`)
- `npm run dev` — Vite dev server
- `npm run build` — production build
- `npm run lint` — ESLint
- `npm run preview` — preview a production build
- No test runner is configured.

The frontend talks to the backend at a hardcoded `http://127.0.0.1:8000/api` (see
`frontend/src/services/api.js`) — there's no env-based API URL switch, so the backend must be running
locally on port 8000 for the dashboard to work.

## Architecture

### Backend layout
- `main.py` — FastAPI app, CORS, startup/shutdown lifecycle (starts the APScheduler `scheduler` and,
  currently, has the message-auto-reply watchdog disabled — see comments in `startup_event`), mounts
  routers under `/api`.
- `database.py` — not a database layer despite the name. Loads `.env`, creates the Supabase client,
  loads `brand.json`, and defines `app_state`: a single in-memory dict holding cross-request state
  (sniper on/off, session status per dressing, current scan feed, etc.). There is no persistence
  layer/ORM; state is either in Supabase, in this in-memory dict, or in flat JSON files on disk
  (`cron_status.json`, `task_history.json`, `scheduled_republish_*.json`, `*_auto_settings.json`, etc. —
  all gitignored, regenerated at runtime).
- `routes/` — thin FastAPI routers, all prefixed `/api` (maintenance further nests `/api/maintenance`):
  `inventory.py` (largest — articles, Gemini-assisted description generation, treat/republish actions),
  `sourcing.py` (the "sniper" — automated scanning/filtering of new listings), `preparer_annonces.py`
  (photo-lot workflow for drafting new listings), `maintenance.py` (browser profile/session controls,
  feature toggles), `price_estimation.py` (not yet mounted in `main.py` — exposes bare functions, no
  `APIRouter`, see the TODO comment there).
- `services/` — the real logic, one concern per file. Notable ones:
  - `session_manager.py` / `session_manager_cloud.py` — Playwright persistent-context browser profiles
    per "dressing" (`ACCOUNTS` / `ACCOUNTS_CLOUD` dicts map account → profile dir → member ID). The local
    version supports interactive manual reconnection (visible browser + Windows notification); the cloud
    version (for the headless VPS) only checks session validity and emails on failure — it never attempts
    interactive reconnection since there's no display.
  - `clemz_automation.py`, `clemz_partage.py`, `clemz_auto_message.py`, `clemz_cdp.py` — drive the Clemz
    browser extension via Playwright/CDP to automate Vinted actions (share favorites, auto-reply to
    buyers, etc.). These are the largest and most fragile modules — Vinted/Clemz DOM changes break them.
  - `automation_service.py` (APScheduler instance + republish cron triggers) and
    `automation_scheduler.py` (JSON-file-backed task history/state helpers) — together implement the
    "republish at a random time in a daily window per dressing" scheduling logic. Keep these two
    separate: `automation_service` is the scheduler/trigger logic, `automation_scheduler` is persistence.
  - `vinted_scraper.py` — scrapes a dressing's own listings (used both locally and by the cloud runner).
  - `supabase_service.py` — all Supabase reads/writes; this is the actual system of record for inventory.
  - `gemini_brouillon_service.py` / `gemini_utils.py` — Gemini calls for AI-generated listing drafts
    (titles/descriptions/pricing) and a shared rate-limiter (`wait_for_gemini_rate_limit`) — reuse it for
    any new Gemini call site rather than adding a second limiter.
  - `risk_guard.py`, `price_intelligence.py`, `price_estimation_service.py` — pricing/anti-ban heuristics.
  - `photo_watcher_service.py`, `photo_grouping.py`, `photo_similarity.py`, `photo_compressor.py` — the
    "preparer annonces" pipeline that watches an incoming photo folder, groups photos into candidate
    listings by similarity, and compresses them before draft creation.
- `cloud_scraper_runner.py` / `infra/` — a second deployment target: a headless Oracle Cloud VPS running
  only the scraping+sync half (no dashboard, no Clemz automation) on a cron schedule, notifying failures
  by email (`services/email_notifier.py`) since there's no one watching a screen. `infra/provision_vps.sh`
  documents the VPS setup; `infra/create_cloud_session.py` is the one-time interactive step (via VNC) to
  seed a logged-in session that the cron job then just validates.
- Numerous `*_diag.py` scripts at the top level (`clemz_diagnostic`, `vinted_draft_creation_diag.py`,
  `photo_clustering_diag.py`, `brouillon_worker_diag.py`, etc.) are standalone debugging entry points, not
  part of the app import graph — safe to ignore unless specifically debugging that subsystem.

### Multi-browser-profile model
Three persistent Playwright browser profiles ("dressings") act as separate Vinted seller accounts, each
with its own member ID and its own copy of the Clemz extension loaded: `clemz_session_chrome` (Dressing 1),
`clemz_session_edge` (Dressing 2), `clemz_session_brave` (Dressing 3, drafts only). These profile
directories are real Chrome/Edge/Brave user-data dirs (gitignored) — treat them as opaque, stateful, and
never safe to run two automations against concurrently (a comment in `session_manager.py` calls out that
scraping and Clemz automation are "jamais exécutés en même temps" — never run at the same time — by
design). `vinted_session_chrome` / `vinted_session_edge` are a separate, mostly-superseded set of profile
dirs kept only for legacy scraper paths.

### Frontend layout
Standard Vite+React app, no state management library — pages under `src/pages/` fetch directly through
`src/services/api.js` (a flat object-of-methods per backend domain: `inventoryService`,
`priceEstimationService`, `maintenanceService`, `preparerAnnoncesService`). Routing is a flat list in
`App.jsx` (react-router-dom), with a persistent `Sidebar`. Styling is Tailwind v4 (via
`@tailwindcss/postcss`), with a custom `Satoshi` font family bundled under `src/fonts/`. The React
Compiler babel plugin is enabled (see `vite.config.js` / frontend README) — be mindful of its rules
(no manual memoization workarounds needed, but also don't fight the compiler with unusual patterns).

## Working in this repo
- Most backend comments and print/log statements are in French; match that convention in files that are
  already French, since it's the existing codebase style, not a translation task in progress.
- Secrets live in `backend/.env` (Notion, Supabase, Gemini, Gmail app password, feature flags). It's
  gitignored — never commit it, and don't assume its current contents are stable (feature flags like
  `CLEMZ_VISIBLE` are meant to be flipped during development).
- A large amount of this repo is generated/runtime state that happens to be tracked oddly by git (browser
  profile internals like `IndexedDB`, `History`, `Favicons` inside `clemz_session_*`/`vinted_session_*`).
  Avoid touching those paths; they are not source code.

## Contexte métier VintedPro (à lire avant toute intervention)

### Vue d'ensemble
Dashboard d'automatisation pour la revente sur Vinted, gérant deux comptes
("Dressing 1" via Chrome/clemz_session_chrome, "Dressing 2" via Edge/
clemz_session_edge"). Long terme : ajouter une couche agent IA (Claude API)
au-dessus de la couche d'exécution existante.

### Stack technique
- Backend : FastAPI (Python) + APScheduler (cron) + Supabase (Postgres)
- Frontend : React/Vite + Tailwind
- Automatisation navigateur : Playwright/CDP + extension Clemz (shadow DOM,
  nécessite CDP, pas les sélecteurs Playwright standards)
- IA : Gemini (gemini-3.1-flash-lite actuellement, rotation de clés en place
  via gemini_utils.py -- quota journalier de 20 req/clé/projet Google Cloud)
- Photos : Syncthing (sync téléphone -> PC) + watcher Python + clustering
  temporel (photo_grouping.py) + second passage de similarité visuelle CLIP
  local (photo_similarity.py, aucun coût Gemini)

### Règles de sécurité anti-détection (JAMAIS à contourner)
- Les deux dressings ne doivent JAMAIS agir en simultané (republication,
  baisse de prix, partage vues/favoris) -- toujours séquentiel avec délai
  aléatoire de 2-5 min entre les deux (voir clemz_automation.py, clemz_partage.py)
- risk_guard.py : quotas quotidiens par dressing/action_type, convalescence
  14j après suspension signalée, repos forcé après 3j actifs consécutifs
- Chrome 137+ interdit --load-extension -- utiliser Chromium Playwright
  embarqué + copie locale nettoyée de l'extension (backend/clemz_extension/)

### Points techniques Clemz appris à la dure
- #miniVinz nécessite de vrais événements souris (mousedown/up 80ms), pas .click()
- ensure_panel_open() doit être appelé à CHAQUE itération de boucle produit
- is_element_visible doit utiliser getBoundingClientRect, pas getComputedStyle
- Version Clemz : parsing sémantique du nom de dossier, jamais os.path.getmtime

### Style de travail attendu
- Toujours donner le diff (AVANT/APRÈS) précis, jamais juste le résultat final
- Toute nouvelle logique de decision (quota, seuil, prix) doit être validée
  par calcul sur des exemples réels avant application -- pas de valeurs
  ajustées à l'aveugle (voir l'historique de calibration de SEUIL_ECART_GROUPE_SECONDES)
- Toujours vérifier la syntaxe après une modification avant de la présenter
  comme terminée

### Chantiers en cours / connus comme temporaires
- DELAI_FINALISATION_SECONDES actuellement à 15s (normalement 600s/10min) --
  réduit temporairement pour accélérer les tests, à restaurer
- Seuils DAILY_THRESHOLDS republication/baisse_prix dans risk_guard.py
  temporairement assouplis (20/jour au lieu de 10) suite à un incident --
  à réévaluer après stabilisation

## Langue
Toujours répondre en français, y compris les résumés de fin de tâche et les
explications -- même quand les commandes exécutées (git, npm, etc.) ou leurs
sorties brutes restent naturellement en anglais.