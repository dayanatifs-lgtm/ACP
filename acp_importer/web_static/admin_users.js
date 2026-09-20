const root = document.getElementById("root");
let users = [];
let sets = [];
let selectedEmail = "";
let detail = null;
let message = "";
let error = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function effectiveHtml(effective) {
  if (!effective) return "<p class='not-available'>No effective permissions.</p>";
  if (effective.isSuperAdmin) {
    return "<p class='success'>Super administrator — all pages and functions.</p>";
  }
  const pages = effective.pages || {};
  const keys = Object.keys(pages);
  if (!keys.length) return "<p class='not-available'>No active permission sets grant access.</p>";
  const catalog = (AuthPerms.getStatus()?.catalog) || [];
  const labelFor = (pageKey, fnKey) => {
    const page = catalog.find(p => p.key === pageKey);
    const fn = page?.functions?.find(f => f.key === fnKey);
    return fn?.label || fnKey;
  };
  const pageLabel = pageKey => catalog.find(p => p.key === pageKey)?.label || pageKey;
  return `<div class="perm-tree">${keys.map(pageKey => `
    <div class="perm-page">
      <strong>${esc(pageLabel(pageKey))}</strong>
      <ul class="perm-effective-fns">
        ${(pages[pageKey] || []).map(fn => `<li>${esc(labelFor(pageKey, fn))}</li>`).join("")}
      </ul>
    </div>`).join("")}</div>`;
}

function render() {
  const canManage = AuthPerms.hasFunction("administration", "manage_user_permissions");
  const assignedIds = new Set((detail?.permissionSets || []).map(s => s.id));
  const available = sets.filter(s => !assignedIds.has(s.id) && s.isActive);
  root.innerHTML = `<div class="app-frame">
    ${AuthPerms.sidebarHtml("/admin/users")}
    <div class="main-panel"><div class="shell">
      <header>
        <p class="eyebrow">ADMINISTRATION</p>
        <h1>User Permissions</h1>
        <p class="subtitle">Assign permission sets to users. Effective access is the union of all active sets.</p>
      </header>
      ${message ? `<p class="success">${esc(message)}</p>` : ""}
      ${error ? `<p class="error">${esc(error)}</p>` : ""}
      <div class="admin-grid">
        <section class="card">
          <h2>Users</h2>
          <ul class="perm-set-list">
            ${users.map(u => `
              <li class="${u.email === selectedEmail ? "selected" : ""}">
                <button type="button" class="perm-set-pick" data-email="${esc(u.email)}">
                  <b>${esc(u.email)}</b>
                  ${u.isSuperAdmin ? '<span class="release-state released">Super admin</span>' : ""}
                  <small>${(u.permissionSets || []).length} set(s)</small>
                </button>
              </li>`).join("") || "<li class='not-available'>No users.</li>"}
          </ul>
        </section>
        <section class="card">
          <h2>${selectedEmail ? esc(selectedEmail) : "Select a user"}</h2>
          ${!detail ? "<p class='not-available'>Choose a user to manage permission sets.</p>" : `
            <h3 class="perm-heading">Assigned permission sets</h3>
            <ul class="assigned-sets">
              ${(detail.permissionSets || []).map(s => `
                <li>
                  <div>
                    <b>${esc(s.name)}</b>
                    <span class="release-state ${s.isActive ? "released" : "archived"}">${s.isActive ? "Active" : "Inactive"}</span>
                    <div class="help">${esc(s.description || "")}</div>
                  </div>
                  <button type="button" class="secondary btn-unassign" data-id="${s.id}" ${canManage ? "" : "disabled"}>Remove</button>
                </li>`).join("") || "<li class='not-available'>No permission sets assigned.</li>"}
            </ul>
            <div class="assign-row">
              <select id="assign-set" ${canManage ? "" : "disabled"}>
                <option value="">Add permission set…</option>
                ${available.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
              </select>
              <button type="button" class="primary" id="btn-assign" ${canManage ? "" : "disabled"}>Assign</button>
            </div>
            <h3 class="perm-heading">Effective permissions</h3>
            ${effectiveHtml(detail.effectivePermissions)}
          `}
        </section>
      </div>
    </div></div>
  </div>`;

  document.querySelectorAll(".perm-set-pick").forEach(btn => {
    btn.addEventListener("click", () => selectUser(btn.dataset.email));
  });
  document.getElementById("btn-assign")?.addEventListener("click", assign);
  document.querySelectorAll(".btn-unassign").forEach(btn => {
    btn.addEventListener("click", () => unassign(Number(btn.dataset.id)));
  });
  mountSessionChrome("/admin/users", { skipNav: true });
}

async function selectUser(email) {
  selectedEmail = email;
  error = "";
  message = "";
  try {
    detail = await api(`/api/admin/users/${encodeURIComponent(email)}`);
    render();
  } catch (e) {
    error = e.message;
    detail = null;
    render();
  }
}

async function assign() {
  const setId = Number(document.getElementById("assign-set")?.value || 0);
  if (!selectedEmail || !setId) return;
  try {
    detail = await api(`/api/admin/users/${encodeURIComponent(selectedEmail)}/permission-sets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permissionSetId: setId }),
    });
    users = (await api("/api/admin/users")).users || [];
    message = "Permission set assigned.";
    error = "";
    render();
  } catch (e) {
    error = e.message;
    render();
  }
}

async function unassign(setId) {
  if (!selectedEmail || !confirm("Remove this permission set from the user?")) return;
  try {
    detail = await api(`/api/admin/users/${encodeURIComponent(selectedEmail)}/permission-sets/${setId}`, {
      method: "DELETE",
    });
    users = (await api("/api/admin/users")).users || [];
    message = "Permission set removed.";
    error = "";
    render();
  } catch (e) {
    error = e.message;
    render();
  }
}

async function boot() {
  await AuthPerms.load();
  if (!AuthPerms.hasPage("administration")) {
    location.href = "/forbidden";
    return;
  }
  try {
    users = (await api("/api/admin/users")).users || [];
    sets = (await api("/api/admin/permission-sets")).permissionSets || [];
    render();
  } catch (e) {
    error = e.message;
    render();
  }
}

boot();
