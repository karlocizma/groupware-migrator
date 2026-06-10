import { requestJSON } from "./api.js";

async function loadStats() {
  const stats = await requestJSON("/api/admin/stats");
  const grid = document.getElementById("stats-grid");
  const cards = [
    { label: "Users", value: stats.users_total },
    { label: "Jobs total", value: stats.jobs_total },
    { label: "Running", value: stats.jobs_running },
    { label: "Completed", value: stats.jobs_completed },
    { label: "Failed", value: stats.jobs_failed },
    { label: "Last 7 days", value: stats.jobs_last_7d, sub: `${stats.success_rate_7d_pct}% success` },
    { label: "Last 30 days", value: stats.jobs_last_30d, sub: `${stats.success_rate_30d_pct}% success` },
    { label: "Items migrated", value: stats.items_migrated_total.toLocaleString() },
    { label: "Batches", value: stats.batches_total },
  ];
  grid.innerHTML = cards
    .map(
      (c) => `<div class="stat-card">
        <div class="stat-label">${c.label}</div>
        <div class="stat-value">${c.value}</div>
        ${c.sub ? `<div class="stat-sub">${c.sub}</div>` : ""}
      </div>`
    )
    .join("");
}

async function loadUsers() {
  const data = await requestJSON("/api/admin/users");
  const tbody = document.getElementById("users-tbody");
  const countEl = document.getElementById("users-count");
  const users = data.items || [];
  countEl.textContent = users.length;
  if (!users.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">No users yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = users
    .map(
      (u) => `<tr data-id="${u.id}">
        <td>${u.email}</td>
        <td>${u.is_admin ? '<span class="badge-admin">admin</span>' : "<span style='color:#8892a4'>user</span>"}</td>
        <td>${u.is_active !== 0 ? '<span class="badge-active">active</span>' : '<span class="badge-inactive">inactive</span>'}</td>
        <td style="color:#8892a4;font-size:0.8rem">${new Date(u.created_at).toLocaleDateString()}</td>
        <td>
          <div class="btn-row">
            <button class="btn-xs toggle-admin-btn" data-id="${u.id}" data-admin="${u.is_admin ? 1 : 0}">
              ${u.is_admin ? "Remove admin" : "Make admin"}
            </button>
            <button class="btn-xs ${u.is_active !== 0 ? "danger" : "primary"} toggle-active-btn" data-id="${u.id}" data-active="${u.is_active !== 0 ? 1 : 0}">
              ${u.is_active !== 0 ? "Deactivate" : "Activate"}
            </button>
          </div>
        </td>
      </tr>`
    )
    .join("");
}

async function loadAuditLog() {
  const data = await requestJSON("/api/admin/audit-log?limit=100");
  const tbody = document.getElementById("audit-tbody");
  const countEl = document.getElementById("audit-count");
  const events = data.items || [];
  countEl.textContent = events.length;
  if (!events.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">No admin actions recorded yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = events
    .map(
      (e) => `<tr>
        <td style="color:#8892a4;font-size:0.8rem;white-space:nowrap">${new Date(e.created_at).toLocaleString()}</td>
        <td style="font-size:0.82rem">${e.admin_id.slice(0, 8)}…</td>
        <td><code style="background:rgba(255,255,255,0.06);border-radius:4px;padding:2px 6px;font-size:0.78rem">${e.action}</code></td>
        <td style="font-size:0.8rem;color:#8892a4">${e.target_id ? e.target_id.slice(0, 8) + "…" : "—"}</td>
        <td style="font-size:0.78rem;color:#8892a4">${Object.keys(e.details || {}).length ? JSON.stringify(e.details) : "—"}</td>
      </tr>`
    )
    .join("");
}

async function init() {
  // Nav user
  try {
    const me = await requestJSON("/auth/me");
    const navUser = document.getElementById("nav-user");
    if (navUser) navUser.textContent = me.email;
    if (!me.is_admin) {
      window.location.href = "/";
    }
  } catch {
    window.location.href = "/login";
  }

  document.getElementById("logout-btn")?.addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });

  await Promise.all([loadStats(), loadUsers(), loadAuditLog()]);

  // Toggle admin / active buttons (delegated)
  document.getElementById("users-tbody")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;

    if (btn.classList.contains("toggle-admin-btn")) {
      const userId = btn.dataset.id;
      const isAdmin = btn.dataset.admin === "1";
      btn.disabled = true;
      btn.textContent = "…";
      await requestJSON(`/api/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ is_admin: !isAdmin }),
      });
      await loadUsers();
    }

    if (btn.classList.contains("toggle-active-btn")) {
      const userId = btn.dataset.id;
      const isActive = btn.dataset.active === "1";
      btn.disabled = true;
      btn.textContent = "…";
      await requestJSON(`/api/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !isActive }),
      });
      await loadUsers();
    }
  });

  // Create user form
  document.getElementById("toggle-create-user")?.addEventListener("click", () => {
    const panel = document.getElementById("create-user-panel");
    const hidden = panel?.hidden;
    if (panel) panel.hidden = !hidden;
  });

  document.getElementById("create-user-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fb = document.getElementById("create-user-feedback");
    const btn = document.getElementById("create-user-btn");
    const email = document.getElementById("new-email").value.trim();
    const password = document.getElementById("new-password").value;
    const isAdmin = document.getElementById("new-is-admin").checked;

    btn.disabled = true;
    btn.textContent = "Creating…";
    fb.style.display = "none";
    try {
      await requestJSON("/auth/users", {
        method: "POST",
        body: JSON.stringify({ email, password, is_admin: isAdmin }),
      });
      document.getElementById("new-email").value = "";
      document.getElementById("new-password").value = "";
      document.getElementById("new-is-admin").checked = false;
      fb.className = "feedback-line ok";
      fb.textContent = `User ${email} created.`;
      fb.style.display = "block";
      await loadUsers();
    } catch (err) {
      fb.className = "feedback-line err";
      fb.textContent = err.message || "Failed to create user.";
      fb.style.display = "block";
    } finally {
      btn.disabled = false;
      btn.textContent = "Create";
    }
  });

  // Audit log refresh
  document.getElementById("refresh-audit")?.addEventListener("click", loadAuditLog);

  // Cleanup
  document.getElementById("cleanup-btn")?.addEventListener("click", async () => {
    const days = parseInt(document.getElementById("cleanup-days").value, 10);
    const resultEl = document.getElementById("cleanup-result");
    const btn = document.getElementById("cleanup-btn");
    if (!days || days < 1) return;
    if (!confirm(`Delete all completed/failed jobs finished more than ${days} days ago?`)) return;
    btn.disabled = true;
    btn.textContent = "Running…";
    resultEl.style.display = "none";
    try {
      const result = await requestJSON("/api/admin/cleanup", {
        method: "POST",
        body: JSON.stringify({ older_than_days: days }),
      });
      resultEl.textContent = `Deleted: ${result.jobs_deleted} jobs, ${result.batches_deleted} batches, ${result.admin_events_deleted} audit events.`;
      resultEl.style.display = "inline";
      await Promise.all([loadStats(), loadAuditLog()]);
    } catch (err) {
      resultEl.textContent = err.message || "Cleanup failed.";
      resultEl.style.display = "inline";
    } finally {
      btn.disabled = false;
      btn.textContent = "Run cleanup";
    }
  });
}

init();
