// @ts-check
import { requestJSON } from "./api.js";
import { withLoading } from "./main.js";

async function bootstrap() {
  try {
    const me = await requestJSON("/auth/me");
    if (me.is_admin) {
      const link = document.getElementById("admin-link");
      if (link) link.style.display = "";
    }
  } catch (_) {}
  await Promise.all([loadSchedules(), loadWebhooks(), loadTotpStatus()]);
  bindScheduleForm();
  bindWebhookForm();
  bindTotpHandlers();
}

// ─── Schedules ──────────────────────────────────────────────────────────────

async function loadSchedules() {
  const list = document.getElementById("schedules-list");
  if (!list) return;
  try {
    const data = await requestJSON("/api/schedules");
    renderSchedules(data.items || []);
  } catch (e) {
    list.innerHTML = `<p style="color:#f87171;font-size:0.85rem;padding:16px">${e.message}</p>`;
  }
}

function renderSchedules(items) {
  const list = document.getElementById("schedules-list");
  if (!list) return;
  if (!items.length) {
    list.innerHTML = `<p style="color:#8892a4;font-size:0.85rem;text-align:center;padding:24px 0">No schedules yet.</p>`;
    return;
  }
  list.innerHTML = items.map(s => `
    <div class="job-row" data-id="${s.id}" style="padding:12px 14px;margin-bottom:8px;border-radius:8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <div>
          <strong style="font-size:0.9rem">${esc(s.name || "(unnamed)")}</strong>
          <span class="pill" style="margin-left:8px;background:${s.is_active ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)"}">
            ${s.is_active ? "active" : "paused"}
          </span>
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn ghost" data-sched-toggle="${s.id}" data-active="${s.is_active}">${s.is_active ? "Pause" : "Resume"}</button>
          <button class="btn ghost" data-sched-delete="${s.id}" style="color:#f87171">Delete</button>
        </div>
      </div>
      <div style="font-size:0.8rem;color:#8892a4;margin-top:4px">
        <code>${esc(s.schedule_expr)}</code> (${esc(s.schedule_type)})
        · Next: ${s.next_run_at ? new Date(s.next_run_at).toLocaleString() : "—"}
        ${s.last_run_at ? `· Last: ${new Date(s.last_run_at).toLocaleString()}` : ""}
      </div>
    </div>
  `).join("");

  list.addEventListener("click", async (e) => {
    const toggleBtn = e.target.closest("[data-sched-toggle]");
    const deleteBtn = e.target.closest("[data-sched-delete]");
    if (toggleBtn) {
      const id = toggleBtn.dataset.schedToggle;
      const currently = toggleBtn.dataset.active === "1";
      await requestJSON(`/api/schedules/${id}`, { method: "PATCH", body: { is_active: !currently } });
      loadSchedules();
    }
    if (deleteBtn) {
      const id = deleteBtn.dataset.schedDelete;
      if (!confirm("Delete this schedule?")) return;
      await requestJSON(`/api/schedules/${id}`, { method: "DELETE" });
      loadSchedules();
    }
  }, { once: true });
}

function bindScheduleForm() {
  // Schedule creation currently disabled — requires building full request from dashboard state.
  // The form button is disabled by default and shows a tooltip.
  // This is intentional: the scheduler page is for managing existing schedules.
  // A full integration would populate the request from saved form state (localStorage).
  const form = document.getElementById("schedule-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("sched-create-btn");
    const feedback = document.getElementById("sched-feedback");
    const name = document.getElementById("sched-name").value;
    const type = document.getElementById("sched-type").value;
    const expr = document.getElementById("sched-expr").value.trim();
    if (!expr) return;

    // Load saved form state from localStorage (populated by main.js form persistence)
    const savedRequest = _loadSavedRequest();
    if (!savedRequest) {
      if (feedback) feedback.textContent = "No saved migration form found. Fill in the migration form on the dashboard first.";
      return;
    }
    try {
      await withLoading(btn, "Creating…", async () => {
        await requestJSON("/api/schedules", {
          method: "POST",
          body: { name, schedule_type: type, schedule_expr: expr, request: savedRequest },
        });
      });
      if (feedback) { feedback.style.color = "#34d399"; feedback.textContent = "Schedule created."; }
      loadSchedules();
    } catch (err) {
      if (feedback) { feedback.style.color = "#f87171"; feedback.textContent = err.message; }
    }
  });
}

function _loadSavedRequest() {
  // Attempt to reconstruct a request dict from localStorage keys written by form.js
  const get = (k) => localStorage.getItem("gm_form_" + k) || "";
  const host = get("source-host");
  if (!host) return null;
  return {
    workload: get("workload") || "mail",
    source: {
      protocol: get("source-protocol") || "imap",
      connection: {
        host: get("source-host"),
        port: parseInt(get("source-port") || "993", 10),
        username: get("source-username") || "",
        password: "",  // cannot save passwords in localStorage
        ssl: get("source-ssl") !== "false",
      },
    },
    destination: {
      protocol: get("destination-protocol") || "imap",
      connection: {
        host: get("destination-host"),
        port: parseInt(get("destination-port") || "993", 10),
        username: get("destination-username") || "",
        password: "",
        ssl: get("destination-ssl") !== "false",
      },
    },
    options: {
      dry_run: false,
      sync_mode: get("sync-mode") || "full",
    },
  };
}

// ─── Webhooks ────────────────────────────────────────────────────────────────

async function loadWebhooks() {
  const list = document.getElementById("webhooks-list");
  if (!list) return;
  try {
    const data = await requestJSON("/api/webhooks");
    renderWebhooks(data.items || []);
  } catch (e) {
    list.innerHTML = `<p style="color:#f87171;font-size:0.85rem;padding:16px">${e.message}</p>`;
  }
}

function renderWebhooks(items) {
  const list = document.getElementById("webhooks-list");
  if (!list) return;
  if (!items.length) {
    list.innerHTML = `<p style="color:#8892a4;font-size:0.85rem;text-align:center;padding:24px 0">No webhooks yet.</p>`;
    return;
  }
  list.innerHTML = items.map(h => `
    <div style="padding:12px 14px;margin-bottom:8px;border-radius:8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <strong style="font-size:0.88rem">${esc(h.label || h.url)}</strong>
          ${h.last_delivery_status ? `<span class="pill" style="margin-left:6px;background:${h.last_delivery_status < 300 ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)"}">${h.last_delivery_status}</span>` : ""}
        </div>
        <button class="btn ghost" data-wh-delete="${h.id}" style="color:#f87171">Delete</button>
      </div>
      <div style="font-size:0.78rem;color:#8892a4;margin-top:4px">
        ${esc(h.url)} · Events: ${(h.events || []).join(", ")}
        ${h.last_delivery_at ? `· Last delivery: ${new Date(h.last_delivery_at).toLocaleString()}` : ""}
      </div>
    </div>
  `).join("");

  list.addEventListener("click", async (e) => {
    const deleteBtn = e.target.closest("[data-wh-delete]");
    if (deleteBtn) {
      const id = deleteBtn.dataset.whDelete;
      if (!confirm("Delete this webhook?")) return;
      await requestJSON(`/api/webhooks/${id}`, { method: "DELETE" });
      loadWebhooks();
    }
  }, { once: true });
}

function bindWebhookForm() {
  const form = document.getElementById("webhook-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("wh-create-btn");
    const feedback = document.getElementById("wh-feedback");
    const secretDisplay = document.getElementById("wh-secret-display");
    const url = document.getElementById("wh-url").value.trim();
    const label = document.getElementById("wh-label").value.trim();
    const events = Array.from(document.querySelectorAll("[name=wh-event]:checked")).map(el => el.value);
    if (!url) return;
    try {
      const result = await withLoading(btn, "Adding…", () =>
        requestJSON("/api/webhooks", { method: "POST", body: { url, label, events } })
      );
      if (feedback) { feedback.style.color = "#34d399"; feedback.textContent = "Webhook created. Copy your secret below — it won't be shown again!"; }
      if (secretDisplay) {
        secretDisplay.style.color = "#fbbf24";
        secretDisplay.innerHTML = `<strong>Secret (copy now):</strong> <code>${esc(result.secret)}</code>`;
      }
      form.reset();
      loadWebhooks();
    } catch (err) {
      if (feedback) { feedback.style.color = "#f87171"; feedback.textContent = err.message; }
    }
  });
}

// ─── TOTP ────────────────────────────────────────────────────────────────────

async function loadTotpStatus() {
  const area = document.getElementById("totp-status-area");
  try {
    const data = await requestJSON("/auth/totp/status");
    if (area) {
      area.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;padding:12px 0">
          <span class="pill" style="background:${data.totp_enabled ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)"}">
            2FA ${data.totp_enabled ? "enabled" : "disabled"}
          </span>
          ${data.totp_enabled
            ? `<button id="totp-show-disable" class="btn secondary">Disable 2FA…</button>`
            : `<button id="totp-show-setup" class="btn secondary">Enable 2FA…</button>`}
        </div>
      `;
      if (data.totp_enabled) {
        document.getElementById("totp-show-disable")?.addEventListener("click", () => {
          document.getElementById("totp-disable-area").style.display = "";
        });
      } else {
        document.getElementById("totp-show-setup")?.addEventListener("click", startTotpSetup);
      }
    }
  } catch (e) {
    if (area) area.innerHTML = `<p style="color:#f87171;font-size:0.85rem">${e.message}</p>`;
  }
}

async function startTotpSetup() {
  try {
    const data = await requestJSON("/auth/totp/setup");
    document.getElementById("totp-secret-display").value = data.secret;
    document.getElementById("totp-uri-display").value = data.uri;
    document.getElementById("totp-recovery-display").textContent = data.recovery_codes.join("\n");
    document.getElementById("totp-setup-area").style.display = "";
    document.getElementById("totp-status-area").style.display = "none";
  } catch (e) {
    alert("Failed to start 2FA setup: " + e.message);
  }
}

function bindTotpHandlers() {
  document.getElementById("totp-confirm-btn")?.addEventListener("click", async () => {
    const code = document.getElementById("totp-confirm-code").value.trim();
    const feedback = document.getElementById("totp-feedback");
    if (!code) return;
    try {
      await requestJSON("/auth/totp/confirm", { method: "POST", body: { code } });
      if (feedback) { feedback.style.color = "#34d399"; feedback.textContent = "2FA enabled successfully. You'll need your authenticator on next login."; }
      document.getElementById("totp-setup-area").style.display = "none";
      document.getElementById("totp-status-area").style.display = "";
      loadTotpStatus();
    } catch (e) {
      if (feedback) { feedback.style.color = "#f87171"; feedback.textContent = e.message; }
    }
  });

  document.getElementById("totp-cancel-btn")?.addEventListener("click", () => {
    document.getElementById("totp-setup-area").style.display = "none";
    document.getElementById("totp-status-area").style.display = "";
  });

  document.getElementById("totp-disable-btn")?.addEventListener("click", async () => {
    const password = document.getElementById("totp-disable-password").value;
    const feedback = document.getElementById("totp-disable-feedback");
    if (!password) return;
    try {
      await requestJSON("/auth/totp/disable", { method: "POST", body: { current_password: password } });
      if (feedback) { feedback.style.color = "#34d399"; feedback.textContent = "2FA disabled."; }
      document.getElementById("totp-disable-area").style.display = "none";
      loadTotpStatus();
    } catch (e) {
      if (feedback) { feedback.style.color = "#f87171"; feedback.textContent = e.message; }
    }
  });
}

function esc(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

bootstrap();
