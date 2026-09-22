const root = document.getElementById("root");
let catalog = [];
let sets = [];
let selectedId = null;
let form = blankForm();
let message = "";
let error = "";
let saving = false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function blankForm() {
  return { id: null, name: "", description: "", isActive: true, grants: [] };
}

function grantKey(pageKey, functionKey) {
  return `${pageKey}::${functionKey}`;
}

function selectedGrantSet() {
  return new Set((form.grants || []).map(g => grantKey(g.pageKey, g.functionKey)));
}

function toggleGrant(pageKey, functionKey, checked) {
  const key = grantKey(pageKey, functionKey);
  const map = new Map((form.grants || []).map(g => [grantKey(g.pageKey, g.functionKey), g]));
  if (checked) map.set(key, { pageKey, functionKey });
  else map.delete(key);
  form.grants = [...map.values()];
}

function togglePage(page, checked) {
  const map = new Map((form.grants || []).map(g => [grantKey(g.pageKey, g.functionKey), g]));
  for (const fn of page.functions) {
    const key = grantKey(page.key, fn.key);
    if (checked) map.set(key, { pageKey: page.key, functionKey: fn.key });
    else map.delete(key);
  }
  form.grants = [...map.values()];
}

function pageChecked(page, selected) {
  return page.functions.every(fn => selected.has(grantKey(page.key, fn.key)));
}

function pageIndeterminate(page, selected) {
  const count = page.functions.filter(fn => selected.has(grantKey(page.key, fn.key))).length;
  return count > 0 && count < page.functions.length;
}

function renderTree() {
  const selected = selectedGrantSet();
  return `<div class="perm-tree">${catalog.map(page => {
    const all = pageChecked(page, selected);
    const partial = pageIndeterminate(page, selected);
    return `<div class="perm-page">
      <label class="perm-page-label">
        <input type="checkbox" data-page="${esc(page.key)}" ${all ? "checked" : ""} ${partial ? "data-indeterminate=1" : ""}/>
        <strong>${esc(page.label)}</strong>
      </label>
      <div class="perm-functions">
        ${page.functions.map(fn => `
          <label>
            <input type="checkbox" data-page-fn="${esc(page.key)}" data-fn="${esc(fn.key)}"
              ${selected.has(grantKey(page.key, fn.key)) ? "checked" : ""}/>
            ${esc(fn.label)}
          </label>`).join("")}
      </div>
    </div>`;
  }).join("")}</div>`;
}

function render() {
  const canManage = AuthPerms.hasFunction("administration", "manage_permission_sets");
  root.innerHTML = `<div class="app-frame">
    ${AuthPerms.sidebarHtml("/admin/permission-sets")}
    <div class="main-panel"><div class="shell">
      <header>
        <p class="eyebrow">ADMINISTRATION</p>
        <h1>Permission Sets</h1>
        <p class="subtitle">Create sets of page and function access, then assign them to users. An active set named <strong>Default</strong> is granted automatically to users who have no sets yet (first login / first page load).</p>
      </header>
      ${message ? `<p class="success">${esc(message)}</p>` : ""}
      ${error ? `<p class="error">${esc(error)}</p>` : ""}
      <div class="admin-grid">
        <section class="card">
          <div class="section-heading">
            <h2>Sets</h2>
            <button type="button" class="primary" id="btn-new" ${canManage ? "" : "disabled"}>New</button>
          </div>
          <ul class="perm-set-list">
            ${sets.map(s => `
              <li class="${s.id === selectedId ? "selected" : ""}">
                <button type="button" class="perm-set-pick" data-id="${s.id}">
                  <b>${esc(s.name)}</b>
                  <span class="release-state ${s.isActive ? "released" : "archived"}">${s.isActive ? "Active" : "Inactive"}</span>
                  <small>${s.grantCount || 0} grants · ${s.userCount || 0} users</small>
                </button>
              </li>`).join("") || "<li class='not-available'>No permission sets yet.</li>"}
          </ul>
        </section>
        <section class="card">
          <h2>${form.id ? "Edit permission set" : "Create permission set"}</h2>
          <div class="perm-form">
            <label class="perm-field">
              <span>Name</span>
              <input id="ps-name" value="${esc(form.name)}" placeholder="e.g. Finance Manager" ${canManage ? "" : "disabled"}/>
            </label>
            <label class="perm-field">
              <span>Description</span>
              <textarea id="ps-desc" rows="2" placeholder="What this set is for" ${canManage ? "" : "disabled"}>${esc(form.description)}</textarea>
            </label>
            <label class="perm-active">
              <input id="ps-active" type="checkbox" ${form.isActive ? "checked" : ""} ${canManage ? "" : "disabled"}/>
              <span class="perm-active-text">
                <strong>Active</strong>
                <small>Inactive sets do not grant any access</small>
              </span>
            </label>
          </div>
          <h3 class="perm-heading">Pages &amp; functions</h3>
          <p class="help" style="margin:0 0 10px">Tick a page to grant all of its actions, or choose actions individually.</p>
          ${renderTree()}
          <div class="buttons" style="margin-top:18px">
            <button type="button" class="primary" id="btn-save" ${canManage ? "" : "disabled"}>${saving ? "Saving…" : "Save changes"}</button>
            ${form.id ? `<button type="button" class="danger" id="btn-delete" ${canManage ? "" : "disabled"}>Delete</button>` : ""}
          </div>
        </section>
      </div>
    </div></div>
  </div>`;

  document.querySelectorAll("input[data-indeterminate]").forEach(el => { el.indeterminate = true; });
  document.getElementById("btn-new")?.addEventListener("click", () => {
    selectedId = null;
    form = blankForm();
    message = "";
    error = "";
    render();
  });
  document.querySelectorAll(".perm-set-pick").forEach(btn => {
    btn.addEventListener("click", async () => {
      selectedId = Number(btn.dataset.id);
      try {
        form = await api(`/api/admin/permission-sets/${selectedId}`);
        form = {
          id: form.id,
          name: form.name,
          description: form.description || "",
          isActive: form.isActive,
          grants: form.grants || [],
        };
        message = "";
        error = "";
        render();
      } catch (e) {
        error = e.message;
        render();
      }
    });
  });
  document.querySelectorAll("input[data-page]").forEach(el => {
    el.addEventListener("change", () => {
      form.name = document.getElementById("ps-name")?.value ?? form.name;
      form.description = document.getElementById("ps-desc")?.value ?? form.description;
      form.isActive = !!document.getElementById("ps-active")?.checked;
      const page = catalog.find(p => p.key === el.dataset.page);
      if (!page) return;
      togglePage(page, el.checked);
      render();
    });
  });
  document.querySelectorAll("input[data-page-fn]").forEach(el => {
    el.addEventListener("change", () => {
      toggleGrant(el.dataset.pageFn, el.dataset.fn, el.checked);
      // Keep form fields
      form.name = document.getElementById("ps-name")?.value ?? form.name;
      form.description = document.getElementById("ps-desc")?.value ?? form.description;
      form.isActive = !!document.getElementById("ps-active")?.checked;
      render();
    });
  });
  document.getElementById("btn-save")?.addEventListener("click", save);
  document.getElementById("btn-delete")?.addEventListener("click", remove);
  mountSessionChrome("/admin/permission-sets", { skipNav: true });
}

async function save() {
  form.name = document.getElementById("ps-name")?.value.trim() || "";
  form.description = document.getElementById("ps-desc")?.value.trim() || "";
  form.isActive = !!document.getElementById("ps-active")?.checked;
  saving = true;
  error = "";
  message = "";
  render();
  try {
    const body = {
      name: form.name,
      description: form.description,
      isActive: form.isActive,
      grants: form.grants,
    };
    const saved = form.id
      ? await api(`/api/admin/permission-sets/${form.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : await api("/api/admin/permission-sets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    selectedId = saved.id;
    form = {
      id: saved.id,
      name: saved.name,
      description: saved.description || "",
      isActive: saved.isActive,
      grants: saved.grants || [],
    };
    message = "Permission set saved.";
    sets = (await api("/api/admin/permission-sets")).permissionSets || [];
  } catch (e) {
    error = e.message;
  } finally {
    saving = false;
    render();
  }
}

async function remove() {
  if (!form.id || !confirm(`Delete permission set "${form.name}"?`)) return;
  try {
    await api(`/api/admin/permission-sets/${form.id}`, { method: "DELETE" });
    selectedId = null;
    form = blankForm();
    message = "Permission set deleted.";
    sets = (await api("/api/admin/permission-sets")).permissionSets || [];
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
    const status = AuthPerms.getStatus();
    catalog = status.catalog || (await api("/api/admin/catalog")).pages || [];
    sets = (await api("/api/admin/permission-sets")).permissionSets || [];
    render();
  } catch (e) {
    error = e.message;
    render();
  }
}

boot();
