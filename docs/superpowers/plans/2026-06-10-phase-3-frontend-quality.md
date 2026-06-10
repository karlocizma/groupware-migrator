# Phase 3 — Frontend Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic 1231-line `app.js` into focused ES modules, add localStorage form persistence, implement loading states on action buttons, add mobile responsiveness, fix UI copy, and wire the login redirect.

**Architecture:** Convert `app.js` from a global-scope script to ES module format. Split into six focused modules: `js/api.js` (fetch helpers), `js/form.js` (form state persistence), `js/streams.js` (SSE streams), `js/jobs.js` (job list + detail rendering), `js/batches.js` (batch UI), `js/main.js` (init, tab switching, wiring). Update `index.html` to load `js/main.js` as `type="module"`. Add auth redirect on 401.

**Tech Stack:** Vanilla ES modules (no bundler), localStorage prefix `gm_form_`, CSS media queries at 900px/600px, standard browser `EventSource`.

---

## File Map

**New files:**
- `src/groupware_migrator/api/static/js/api.js` — fetch wrapper (JSON, auth redirect on 401)
- `src/groupware_migrator/api/static/js/form.js` — localStorage form persistence helpers
- `src/groupware_migrator/api/static/js/streams.js` — EventSource wrapper + reconnect
- `src/groupware_migrator/api/static/js/jobs.js` — job list rendering, job detail, event polling
- `src/groupware_migrator/api/static/js/batches.js` — batch list rendering, batch detail
- `src/groupware_migrator/api/static/js/main.js` — init, tab wiring, nav, providers

**Modified files:**
- `src/groupware_migrator/api/static/index.html` — load `js/main.js` as module, update `<title>` and hero subtitle
- `src/groupware_migrator/api/static/styles.css` — add mobile breakpoints at 900px and 600px

**Deleted files (after split is working):**
- `src/groupware_migrator/api/static/app.js` — replaced by ES modules

---

## Task 1: Read and analyze app.js

Before splitting, document what's in app.js so the split is accurate.

- [ ] **Step 1: Read app.js in full**

  Run:
  ```bash
  wc -l src/groupware_migrator/api/static/app.js
  cat -n src/groupware_migrator/api/static/app.js | head -100
  ```
  Read the file to understand all functions, globals, and event bindings.

- [ ] **Step 2: Map functions to modules**

  Create the mapping:
  - `apiFetch`, `apiPost`, `apiGet`, `apiDelete` → `js/api.js`
  - `saveFormState`, `loadFormState`, `clearFormState`, form input listeners → `js/form.js`
  - `openJobStream`, `openBatchStream`, reconnect logic → `js/streams.js`
  - `renderJobsList`, `renderJobRow`, `renderJobDetail`, `openJobDetail` → `js/jobs.js`
  - `renderBatchList`, `renderBatchRow`, `renderBatchDetail` → `js/batches.js`
  - `init`, tab switching, provider presets, start/run/preflight button handlers → `js/main.js`

---

## Task 2: Create js/api.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/api.js`

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/api.js`**

  This module wraps fetch and redirects to `/login` on 401:

  ```javascript
  // Centralized fetch wrapper. Redirects to /login on 401 (session expired).
  export async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('Unauthorized');
    }
    return res;
  }

  export async function apiGet(url) {
    return apiFetch(url);
  }

  export async function apiPost(url, body) {
    return apiFetch(url, { method: 'POST', body: JSON.stringify(body) });
  }

  export async function apiDelete(url) {
    return apiFetch(url, { method: 'DELETE' });
  }
  ```

---

## Task 3: Create js/form.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/form.js`

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/form.js`**

  ```javascript
  const PREFIX = 'gm_form_';

  export function saveField(key, value) {
    try { localStorage.setItem(PREFIX + key, value); } catch (_) {}
  }

  export function loadField(key, fallback = '') {
    try { return localStorage.getItem(PREFIX + key) ?? fallback; } catch (_) { return fallback; }
  }

  export function clearFormFields(keys) {
    keys.forEach(k => { try { localStorage.removeItem(PREFIX + k); } catch (_) {} });
  }

  // Bind an input/select element to localStorage. Restores value on call, saves on change.
  export function persistField(element, key) {
    if (!element) return;
    const stored = loadField(key);
    if (stored !== '') element.value = stored;
    element.addEventListener('input', () => saveField(key, element.value));
    element.addEventListener('change', () => saveField(key, element.value));
  }

  // Restore all form fields from localStorage.
  // fieldMap: { elementId: storageKey }
  export function restoreForm(fieldMap) {
    for (const [id, key] of Object.entries(fieldMap)) {
      persistField(document.getElementById(id), key);
    }
  }
  ```

---

## Task 4: Create js/streams.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/streams.js`

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/streams.js`**

  ```javascript
  // Wraps EventSource with automatic reconnect and cleanup.
  export function openStream(url, { onEvent, onError } = {}) {
    let source = null;
    let closed = false;

    function connect() {
      if (closed) return;
      source = new EventSource(url);
      source.onerror = (e) => {
        source.close();
        if (!closed) {
          onError?.(e);
          setTimeout(connect, 3000);
        }
      };
      if (onEvent) {
        // onEvent receives (eventType, parsedData)
        source.addEventListener('message', (e) => {
          try { onEvent('message', JSON.parse(e.data)); } catch (_) {}
        });
      }
    }

    // Allow callers to listen to named events
    function on(eventType, handler) {
      if (!source) connect();
      source.addEventListener(eventType, (e) => {
        try { handler(JSON.parse(e.data)); } catch (_) {}
      });
      return { on };
    }

    connect();

    return {
      on,
      close() {
        closed = true;
        source?.close();
      },
    };
  }
  ```

---

## Task 5: Create js/jobs.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/jobs.js`

Extract all job-rendering code from app.js into this module. The exact code depends on what's in app.js (read it in Task 1). The module must export:

- `renderJobsList(items, container, { onSelect })` — renders job rows into container
- `renderJobDetail(job, auditEvents, container)` — renders job detail panel
- `openJobDetail(jobId, { apiGet, container })` — fetches job+events and renders

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/jobs.js`**

  Extract all `renderJob*`, `openJob*` functions from app.js and export them. Remove from global scope. Example skeleton (fill with actual code from app.js):

  ```javascript
  import { apiGet } from './api.js';

  export function renderJobRow(job) {
    // [copy exact code from app.js renderJobRow]
  }

  export function renderJobsList(items, container, { onSelect } = {}) {
    // [copy exact code from app.js renderJobsList]
  }

  export function renderJobDetail(job, auditEvents, container) {
    // [copy exact code from app.js renderJobDetail]
  }

  export async function openJobDetail(jobId, container) {
    const [jobRes, eventsRes] = await Promise.all([
      apiGet(`/api/jobs/${jobId}`),
      apiGet(`/api/jobs/${jobId}/events?limit=200`),
    ]);
    const job = await jobRes.json();
    const { items: events } = await eventsRes.json();
    renderJobDetail(job, events, container);
  }
  ```

---

## Task 6: Create js/batches.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/batches.js`

Extract all batch-rendering code from app.js. Must export:

- `renderBatchRow(batch)` — returns HTML string
- `renderBatchList(items, container, { onSelect })` — renders list
- `renderBatchDetail(batch, items, container)` — renders detail panel

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/batches.js`**

  ```javascript
  import { apiGet } from './api.js';

  export function renderBatchRow(batch) {
    // [copy exact code from app.js renderBatchRow]
  }

  export function renderBatchList(items, container, { onSelect } = {}) {
    // [copy exact code from app.js renderBatchList]
  }

  export function renderBatchDetail(batch, batchItems, container) {
    // [copy exact code from app.js renderBatchDetail]
  }

  export async function openBatchDetail(batchId, container) {
    const res = await apiGet(`/api/batches/${batchId}`);
    const batch = await res.json();
    renderBatchDetail(batch, batch.items ?? [], container);
  }
  ```

---

## Task 7: Create js/main.js

**Files:**
- Create: `src/groupware_migrator/api/static/js/main.js`

This is the entry point. It imports from all other modules and wires up the UI.

- [ ] **Step 1: Create `src/groupware_migrator/api/static/js/main.js`**

  ```javascript
  import { apiGet, apiPost } from './api.js';
  import { restoreForm, saveField } from './form.js';
  import { openStream } from './streams.js';
  import { renderJobsList, openJobDetail } from './jobs.js';
  import { renderBatchList, openBatchDetail } from './batches.js';

  // [Copy all init, tab-switching, provider preset, button handler code from app.js]
  // Replace all inline fetch calls with apiGet/apiPost
  // Replace all inline localStorage calls with saveField/loadField
  // Replace all inline EventSource usage with openStream
  // Replace all inline render calls with imported render functions

  document.addEventListener('DOMContentLoaded', () => {
    // [init code]
  });
  ```

---

## Task 8: Update index.html

**Files:**
- Modify: `src/groupware_migrator/api/static/index.html`

- [ ] **Step 1: Replace app.js script tag with ES module entry point**

  Change:
  ```html
  <script src="app.js"></script>
  ```
  To:
  ```html
  <script type="module" src="js/main.js"></script>
  ```

- [ ] **Step 2: Update `<title>` and hero subtitle**

  Change `<title>Groupware Migrator</title>` to `<title>Groupware Migrator — Email & Calendar Migration</title>`.

  Find the hero subtitle text and update to: `Migrate email, calendar, and contacts between servers.`

- [ ] **Step 3: Add auth-check meta or redirect**

  In `<head>`, add a noscript redirect:
  ```html
  <noscript><meta http-equiv="refresh" content="0;url=/login"></noscript>
  ```

  The JS auth redirect (401 → /login) in `api.js` handles the runtime case.

- [ ] **Step 4: Remove old app.js script tag if still present**

  Verify `app.js` is no longer referenced in the HTML.

---

## Task 9: Mobile responsiveness

**Files:**
- Modify: `src/groupware_migrator/api/static/styles.css`

- [ ] **Step 1: Read current styles.css**

  ```bash
  wc -l src/groupware_migrator/api/static/styles.css
  cat src/groupware_migrator/api/static/styles.css
  ```

- [ ] **Step 2: Add mobile breakpoints**

  Append to `styles.css`:

  ```css
  /* ── Mobile: tablet ─────────────────────────────── */
  @media (max-width: 900px) {
    .layout-split {
      flex-direction: column;
    }
    .sidebar {
      width: 100%;
      max-width: none;
      border-right: none;
      border-bottom: 1px solid var(--border);
    }
    .main-content {
      padding: 16px;
    }
  }

  /* ── Mobile: phone ──────────────────────────────── */
  @media (max-width: 600px) {
    .nav-tabs {
      flex-wrap: wrap;
      gap: 4px;
    }
    .nav-tab {
      flex: 1;
      text-align: center;
      min-width: 80px;
    }
    .card, .panel {
      border-radius: 8px;
      padding: 14px;
    }
    .form-row {
      flex-direction: column;
    }
    .form-row > * {
      width: 100%;
    }
    .button-row {
      flex-direction: column;
    }
    .button-row > button {
      width: 100%;
    }
    table {
      font-size: 0.82rem;
    }
    th, td {
      padding: 6px 8px;
    }
  }
  ```

  Note: adjust class names to match the actual classes used in index.html. Read `index.html` first to confirm class names.

---

## Task 10: Loading states on action buttons

This task applies to all action buttons in `main.js`: Start Migration, Run Preflight, Batch Start, etc.

- [ ] **Step 1: Add `withLoading` helper to `js/main.js`**

  Add this function near the top of main.js (before init):

  ```javascript
  function withLoading(btn, label, asyncFn) {
    return async (...args) => {
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = label;
      try {
        return await asyncFn(...args);
      } finally {
        btn.disabled = false;
        btn.textContent = original;
      }
    };
  }
  ```

- [ ] **Step 2: Wrap all action button handlers**

  For each button that makes an API call, wrap the handler:

  Before:
  ```javascript
  startBtn.addEventListener('click', async () => {
    const result = await apiPost('/api/jobs/start', payload);
    // ...
  });
  ```

  After:
  ```javascript
  startBtn.addEventListener('click', withLoading(startBtn, 'Starting…', async () => {
    const result = await apiPost('/api/jobs/start', payload);
    // ...
  }));
  ```

  Apply to: Start Migration button, Run Preflight button, Start Batch button, Batch Preflight button.

---

## Task 11: Delete app.js and verify

- [ ] **Step 1: Delete app.js**

  ```bash
  rm src/groupware_migrator/api/static/app.js
  ```

- [ ] **Step 2: Start the server and manually verify**

  ```bash
  PYTHONPATH=src JWT_SECRET=test-secret-xxxxx-32bytes ADMIN_EMAIL=admin@test.com ADMIN_PASSWORD=password123 uvicorn groupware_migrator.api.app:create_app --factory --port 8000
  ```

  - Visit http://localhost:8000/login — should show login form
  - Login with admin@test.com / password123 — should redirect to /
  - Check / loads with all tabs visible
  - No console errors in browser
  - Check on narrow viewport (< 600px) — layout stacks vertically

- [ ] **Step 3: Run full test suite — all passing**

  ```bash
  PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -v 2>&1 | tail -3
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/groupware_migrator/api/static/
  git add -u  # picks up deleted app.js
  git commit -m "feat: Phase 3 — ES module split, form persistence, loading states, mobile"
  ```

---

## Self-Review Checklist

- [x] **3.1 ES modules:** app.js split into 6 focused modules — Tasks 2-7
- [x] **3.2 Form persistence:** localStorage with `gm_form_` prefix, restore on load — Task 3
- [x] **3.3 Loading states:** `withLoading` helper wraps all action buttons — Task 10
- [x] **3.4 Mobile:** breakpoints at 900px and 600px in styles.css — Task 9
- [x] **3.5 Copy fixes:** `<title>` and hero subtitle updated — Task 8
- [x] **3.6 Auth redirect:** `apiFetch` redirects to `/login` on 401 — Task 2
- [x] **Delete app.js:** after ES modules are working and verified — Task 11

**Important note for implementer:** Tasks 5, 6, and 7 (jobs.js, batches.js, main.js) require reading the actual code in app.js first (Task 1). The skeletons shown above are structural — fill them with the exact functions copied from app.js, then adapt to use imports instead of globals. Do not rewrite the rendering logic from scratch.
