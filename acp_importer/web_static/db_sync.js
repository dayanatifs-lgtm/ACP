const root = document.getElementById("root");
let status = { running: false, message: "Ready", results: [], compare: null, completed: 0, total: 0, phase: "idle" };
let schemas = [];
let components = [];
let selectedComponents = new Set();
let tables = [];
let selected = new Set();
let tableSearch = "";
let componentSearch = "";
let failedSearch = "";
let error = "";
let info = "";

const form = {
  sourceHost: "10.242.66.100",
  sourcePort: "1521",
  sourceService: "thorcfg1_1",
  sourceConnectAs: "service",
  sourceUser: "IFSINFO",
  sourcePassword: "",
  targetHost: "",
  targetPort: "1521",
  targetService: "",
  targetConnectAs: "service",
  targetUser: "",
  targetPassword: "",
  thick: true,
  schema: "",
  addMissingColumns: true,
  createMissingTables: true,
  replaceData: false,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
}

function detailMessage(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => item.msg || JSON.stringify(item)).join("; ");
  }
  return String(detail);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage(data.detail));
  return data;
}

function endpoint(prefix) {
  return {
    host: form[`${prefix}Host`],
    port: Number(form[`${prefix}Port`] || 1521),
    service: form[`${prefix}Service`],
    user: form[`${prefix}User`],
    password: form[`${prefix}Password`],
    connectAs: form[`${prefix}ConnectAs`] || "service",
    thick: !!form.thick,
  };
}

function readForm() {
  const get = id => document.getElementById(id)?.value ?? "";
  const checked = id => !!document.getElementById(id)?.checked;
  form.sourceHost = get("src-host").trim();
  form.sourcePort = get("src-port").trim() || "1521";
  form.sourceService = get("src-service").trim();
  form.sourceConnectAs = get("src-connect-as") || "service";
  form.sourceUser = get("src-user").trim();
  form.sourcePassword = get("src-password");
  form.targetHost = get("tgt-host").trim();
  form.targetPort = get("tgt-port").trim() || "1521";
  form.targetService = get("tgt-service").trim();
  form.targetConnectAs = get("tgt-connect-as") || "service";
  form.targetUser = get("tgt-user").trim();
  form.targetPassword = get("tgt-password");
  form.thick = checked("opt-thick");
  form.schema = get("schema").trim();
  form.addMissingColumns = checked("opt-add-cols");
  form.createMissingTables = checked("opt-create-tables");
  form.replaceData = checked("opt-replace");
  syncComponentSelectionFromDom();
}

function componentName(entry) {
  return typeof entry === "string" ? entry : entry.name;
}

function syncComponentSelectionFromDom() {
  document.querySelectorAll("input[data-component]").forEach(box => {
    const name = box.getAttribute("data-component");
    if (!name) return;
    if (box.checked) selectedComponents.add(name);
    else selectedComponents.delete(name);
  });
}

function filteredComponents() {
  const query = componentSearch.trim().toLowerCase();
  if (!query) return components;
  return components.filter(entry => {
    const name = componentName(entry);
    return String(name).toLowerCase().includes(query);
  });
}

function componentListBlock() {
  if (!components.length) {
    return `<p class="help">Load components to filter tables by IFS module. Uncheck components you want to skip, then Load tables.</p>`;
  }
  const visible = filteredComponents();
  const list = visible.length
    ? `<div class="packages" style="max-height:220px;overflow:auto">${visible.map(entry => {
        const name = componentName(entry);
        const count = typeof entry === "string" ? null : entry.tableCount;
        const label = count == null ? name : `${name} (${count})`;
        const id = `cmp-${name}`;
        return `<label class="check-row"><input type="checkbox" id="${esc(id)}" data-component="${esc(name)}" ${selectedComponents.has(name) ? "checked" : ""}/> ${esc(label)}</label>`;
      }).join("")}</div>`
    : `<p class="help">No components match “${esc(componentSearch)}”.</p>`;
  return `
      <div class="buttons" style="margin:10px 0;gap:8px;display:flex;flex-wrap:wrap;align-items:center">
        <input id="component-search" value="${esc(componentSearch)}" placeholder="Search components…" style="flex:1;min-width:220px;min-height:42px;padding:9px 11px;border:1px solid #b7c4d8;border-radius:8px;font:inherit"/>
        <button type="button" class="secondary" id="component-search-btn">Search</button>
        <button type="button" class="secondary" id="component-search-clear" ${componentSearch ? "" : "disabled"}>Clear</button>
      </div>
      <p class="help">${visible.length} of ${components.length} component(s) shown · ${selectedComponents.size} selected · unchecked components are skipped</p>
      ${list}
      <div class="buttons" style="margin-top:10px">
        <button type="button" class="secondary" id="select-all-components" ${status.running ? "disabled" : ""}>Select all components</button>
        <button type="button" class="secondary" id="select-visible-components" ${status.running || !visible.length ? "disabled" : ""}>Select visible components</button>
        <button type="button" class="secondary" id="clear-components" ${status.running ? "disabled" : ""}>Clear components</button>
      </div>`;
}

function nav() {
  if (typeof AuthPerms !== "undefined") return AuthPerms.sidebarHtml("/db-sync");
  return `<aside class="sidebar">
    <div class="sidebar-brand"><span class="sidebar-mark" aria-hidden="true"></span><span>ACP IMPORTER</span></div>
    <div class="sidebar-section">Tools</div>
    <nav class="sidebar-nav sidebar-nav-tools"><a class="active" href="/db-sync">DB Sync</a></nav>
    <div class="sidebar-footer"><div class="sidebar-user" id="sidebar-user"></div><button type="button" id="sign-out" class="secondary sidebar-signout">Sign out</button></div>
  </aside>`;
}

function endpointFields(prefix, title) {
  const p = prefix === "src" ? "source" : "target";
  const connectAs = form[p + "ConnectAs"] || "service";
  return `<section class="card settings">
    <h2>${title}</h2>
    <label>Host <input id="${prefix}-host" value="${esc(form[p + "Host"])}" placeholder="10.x.x.x"/></label>
    <label>Port <input id="${prefix}-port" value="${esc(form[p + "Port"])}"/></label>
    <label>Connect as <select id="${prefix}-connect-as">
      <option value="service" ${connectAs === "service" ? "selected" : ""}>Service name</option>
      <option value="sid" ${connectAs === "sid" ? "selected" : ""}>SID</option>
    </select></label>
    <label>Service / SID <input id="${prefix}-service" value="${esc(form[p + "Service"])}" placeholder="thorcfg1_1"/></label>
    <label>Username <input id="${prefix}-user" value="${esc(form[p + "User"])}"/></label>
    <label>Password <input id="${prefix}-password" type="password" value="${esc(form[p + "Password"])}" autocomplete="off"/></label>
    <div class="buttons">
      <button type="button" class="secondary" data-test="${p}" ${status.running ? "disabled" : ""}>Test connection</button>
    </div>
  </section>`;
}

function compareBlock() {
  if (!status.compare || !status.compare.tables) return "";
  const rows = status.compare.tables.map(t => {
    const missing = (t.missingColumns || []).map(c => c.name).join(", ") || "—";
    return `<tr>
      <td>${esc(t.table)}</td>
      <td>${esc(t.status)}</td>
      <td>${esc(missing)}</td>
    </tr>`;
  }).join("");
  return `<section class="card"><h2>Compare result</h2>
    ${status.compare.tableCount ? `<p class="help">${status.compare.issueCount || 0} table(s) need attention out of ${status.compare.tableCount}. OK tables are hidden.</p>` : ""}
    <div class="items-table-wrap"><table class="items-table">
      <thead><tr><th>Table</th><th>Status</th><th>Missing columns on target</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></section>`;
}

function resultsBlock() {
  if (!status.results || !status.results.length) return "";
  const latest = status.results.slice(-20).reverse();
  const hidden = (status.resultsTotal || status.results.length) - latest.length;
  const rows = latest.map(r => `<li><b class="${r.success ? "success" : "failed"}">${r.success ? "OK" : "FAIL"}</b> <b>${esc(r.table)}</b><p>${esc(r.message)}</p></li>`).join("");
  const more = hidden > 0 ? `<p class="help">Showing the latest ${latest.length} of ${status.resultsTotal || status.results.length} finished tables.</p>` : "";
  return `<section class="card"><h2>Recent tables</h2>${more}<ul class="results">${rows}</ul></section>`;
}

function failedTablesBlock() {
  const failed = status.failedTables || [];
  if (!failed.length) return "";
  const query = failedSearch.trim().toLowerCase();
  const filtered = query
    ? failed.filter(row => String(row.table || "").toLowerCase().includes(query) || String(row.message || "").toLowerCase().includes(query))
    : failed;
  const rows = filtered.map(row => `<tr>
      <td>${esc(row.table)}</td>
      <td>${esc(row.message)}</td>
    </tr>`).join("") || `<tr><td colspan="2">No failed tables match “${esc(failedSearch)}”.</td></tr>`;
  return `<section class="card">
    <h2>Failed tables (${failed.length})</h2>
    <div class="buttons" style="margin-bottom:10px;gap:8px;display:flex;flex-wrap:wrap;align-items:center">
      <input id="failed-search" value="${esc(failedSearch)}" placeholder="Search failed tables…" style="flex:1;min-width:220px;min-height:42px;padding:9px 11px;border:1px solid #b7c4d8;border-radius:8px;font:inherit"/>
      <button type="button" class="secondary" id="failed-search-btn">Search</button>
      <button type="button" class="secondary" id="failed-search-clear" ${failedSearch ? "" : "disabled"}>Clear</button>
      <button type="button" class="secondary" id="select-failed" ${status.running ? "disabled" : ""}>Select failed in table list</button>
    </div>
    <p class="help">Showing ${filtered.length} of ${failed.length} failed table(s).</p>
    <div class="items-table-wrap" style="max-height:320px;overflow:auto"><table class="items-table">
      <thead><tr><th>Table</th><th>Error</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </section>`;
}

function filteredTables() {
  const query = tableSearch.trim().toLowerCase();
  if (!query) return tables;
  return tables.filter(name => String(name).toLowerCase().includes(query));
}

function tableListBlock() {
  if (!tables.length) {
    return `<p class="help">Choose schema, optionally select components above, then Load tables.</p>`;
  }
  const visible = filteredTables();
  const list = visible.length
    ? `<div class="packages" style="max-height:280px;overflow:auto">${visible.map(t => {
        const id = `tbl-${t}`;
        return `<label class="check-row"><input type="checkbox" id="${esc(id)}" data-table="${esc(t)}" ${selected.has(t) ? "checked" : ""}/> ${esc(t)}</label>`;
      }).join("")}</div>`
    : `<p class="help">No tables match “${esc(tableSearch)}”.</p>`;
  return `
      <div class="buttons" style="margin:10px 0;gap:8px;display:flex;flex-wrap:wrap;align-items:center">
        <input id="table-search" value="${esc(tableSearch)}" placeholder="Search tables…" style="flex:1;min-width:220px;min-height:42px;padding:9px 11px;border:1px solid #b7c4d8;border-radius:8px;font:inherit"/>
        <button type="button" class="secondary" id="table-search-btn">Search</button>
        <button type="button" class="secondary" id="table-search-clear" ${tableSearch ? "" : "disabled"}>Clear</button>
      </div>
      <p class="help">${visible.length} of ${tables.length} table(s) shown · ${selected.size} selected</p>
      ${list}
      <div class="buttons" style="margin-top:10px">
        <button type="button" class="secondary" id="select-all" ${status.running ? "disabled" : ""}>Select all tables</button>
        <button type="button" class="secondary" id="select-visible" ${status.running || !visible.length ? "disabled" : ""}>Select visible tables</button>
        <button type="button" class="secondary" id="select-none" ${status.running ? "disabled" : ""}>Clear tables</button>
      </div>`;
}

function statusBlock() {
  const table = status.current_table || "—";
  const action = status.current_action || status.phase || "idle";
  const progress = status.total ? `${status.completed} finished / ${status.total} selected` : "";
  const rows = Number(status.rows_copied || 0);
  return `<section class="card">
    <h2>Status</h2>
    <p class="${status.running ? "progress" : ""}">${esc(status.message || "Ready")}</p>
    <p><b>Current table:</b> ${esc(table)}</p>
    <p><b>Step:</b> ${esc(action)}${progress ? ` · ${esc(progress)}` : ""}${rows ? ` · ${rows} row(s) copied on this table` : ""}</p>
    ${(status.succeededCount || status.failedCount || status.skippedCount) ? `<p><b>Last result:</b> ${status.succeededCount || 0} copied, ${status.skippedCount || 0} left unchanged, ${status.failedCount || 0} failed</p>` : ""}
  </section>`;
}

function render() {
  const schemaOptions = [`<option value="">Select schema…</option>`]
    .concat(schemas.map(s => `<option value="${esc(s)}" ${form.schema === s ? "selected" : ""}>${esc(s)}</option>`))
    .join("");
  const thickDir = (status.thick && status.thick.libDir) || "";
  const dbhomeWarn = /dbhome/i.test(thickDir)
    ? " Using a full Oracle dbhome as the client often causes ORA-12569 — install Instant Client and set ORACLE_CLIENT_LIB_DIR."
    : "";

  root.innerHTML = `<div class="app-frame">${nav()}<div class="main-panel"><div class="shell">
    <header>
      <p class="eyebrow">ORACLE CFG → DEV</p>
      <h1>DB Sync</h1>
      <p class="subtitle">Compare source/target tables, optionally add missing columns on DEV, then copy rows in FK-aware order. Passwords stay in memory only (not saved).</p>
    </header>
    <p class="help">Each table copies at most 1,000 rows. Smaller tables are copied in full. Larger tables copy the latest 1,000 rows, using that table’s own date, version, or key column. If no such column exists, the first 1,000 rows are copied and the table is logged as unordered.</p>
    ${error ? `<p class="error">${esc(error)}</p>` : ""}
    ${info ? `<p class="success">${esc(info)}</p>` : ""}
    <section class="card settings">
      <h2>Client mode</h2>
      <label class="check-row"><input id="opt-thick" type="checkbox" ${form.thick ? "checked" : ""}/> Use Oracle Instant Client (thick mode) — required when DB uses network encryption</label>
      <p class="help">Set <code>ORACLE_CLIENT_LIB_DIR</code> to Instant Client (<code>oci.dll</code>), not dbhome_1. Restart start_server.bat after pull. Current client: ${esc(thickDir || "not initialized")}${status.thick && status.thick.configDir ? `; TNS_ADMIN ${esc(status.thick.configDir)}` : ""}${dbhomeWarn}${status.thick && status.thick.error ? ` — ${esc(status.thick.error)}` : ""}</p>
    </section>
    <div class="db-sync-endpoints">
      ${endpointFields("src", "Source (CFG)")}
      ${endpointFields("tgt", "Target (DEV)")}
    </div>
    <section class="card settings">
      <h2>Tables</h2>
      <div class="buttons">
        <button type="button" class="secondary" id="load-schemas" ${status.running ? "disabled" : ""}>Load schemas from source</button>
      </div>
      <label>Schema <select id="schema">${schemaOptions}</select></label>
      <div class="buttons">
        <button type="button" class="secondary" id="load-components" ${status.running || !form.schema ? "disabled" : ""}>Load components</button>
        <button type="button" class="secondary" id="load-tables" ${status.running || !form.schema ? "disabled" : ""}>Load tables</button>
        <button type="button" class="secondary" id="compare" ${status.running || !selected.size ? "disabled" : ""}>Compare selected</button>
      </div>
      <h3 style="margin:14px 0 6px;font-size:15px;color:#35435b">Components</h3>
      ${componentListBlock()}
      <h3 style="margin:18px 0 6px;font-size:15px;color:#35435b">Tables</h3>
      ${tableListBlock()}
      <label class="check-row"><input id="opt-create-tables" type="checkbox" ${form.createMissingTables ? "checked" : ""}/> Create missing tables on DEV</label>
      <label class="check-row"><input id="opt-add-cols" type="checkbox" ${form.addMissingColumns ? "checked" : ""}/> Add missing columns on DEV (nullable)</label>
      <label class="check-row"><input id="opt-replace" type="checkbox" ${form.replaceData ? "checked" : ""}/> Replace target rows (DELETE then INSERT) — full sync only</label>
      <div class="buttons">
        <button type="button" class="primary" id="start-sync" ${status.running || !selected.size ? "disabled" : ""}>${status.running ? "Syncing…" : "Start sync"}</button>
        <button type="button" class="secondary" id="retry-failed" ${status.running || !((status.failedCount || 0) > 0 || (status.selectedCount || 0) > 0) ? "disabled" : ""}>Retry failed only</button>
        <button type="button" class="danger" id="stop-sync" ${!status.running ? "disabled" : ""}>Stop</button>
      </div>
      <p class="help">Retry checks source and target row counts. Tables that already match are left unchanged and are not deleted. Only failed or short tables are copied again.</p>
    </section>
    ${statusBlock()}
    ${failedTablesBlock()}
    ${compareBlock()}
    ${resultsBlock()}
  </div></div></div>`;

  document.querySelectorAll("[data-test]").forEach(btn => {
    btn.onclick = () => testEndpoint(btn.getAttribute("data-test"));
  });
  document.getElementById("load-schemas").onclick = loadSchemas;
  document.getElementById("load-components").onclick = loadComponents;
  document.getElementById("load-tables").onclick = loadTables;
  document.getElementById("compare").onclick = compareSelected;
  document.getElementById("start-sync").onclick = () => startSync(false);
  document.getElementById("retry-failed").onclick = () => startSync(true);
  document.getElementById("stop-sync").onclick = stopSync;

  const tableSearchInput = document.getElementById("table-search");
  const tableSearchBtn = document.getElementById("table-search-btn");
  const tableSearchClear = document.getElementById("table-search-clear");
  if (tableSearchBtn) {
    tableSearchBtn.onclick = () => {
      tableSearch = tableSearchInput?.value.trim() || "";
      render();
    };
  }
  if (tableSearchClear) {
    tableSearchClear.onclick = () => {
      tableSearch = "";
      render();
    };
  }
  if (tableSearchInput) {
    tableSearchInput.onkeydown = event => {
      if (event.key === "Enter") {
        event.preventDefault();
        tableSearch = tableSearchInput.value.trim();
        render();
      }
    };
  }

  const failedSearchInput = document.getElementById("failed-search");
  const failedSearchBtn = document.getElementById("failed-search-btn");
  const failedSearchClear = document.getElementById("failed-search-clear");
  const selectFailed = document.getElementById("select-failed");
  if (failedSearchBtn) {
    failedSearchBtn.onclick = () => {
      failedSearch = failedSearchInput?.value.trim() || "";
      render();
    };
  }
  if (failedSearchClear) {
    failedSearchClear.onclick = () => {
      failedSearch = "";
      render();
    };
  }
  if (failedSearchInput) {
    failedSearchInput.onkeydown = event => {
      if (event.key === "Enter") {
        event.preventDefault();
        failedSearch = failedSearchInput.value.trim();
        render();
      }
    };
  }
  if (selectFailed) {
    selectFailed.onclick = () => {
      (status.failedTables || []).forEach(row => {
        const name = row.table;
        if (name && name !== "*" && tables.includes(name)) selected.add(name);
      });
      info = `Selected ${selected.size} table(s), including failed ones found in the loaded list.`;
      render();
    };
  }

  const componentSearchInput = document.getElementById("component-search");
  const componentSearchBtn = document.getElementById("component-search-btn");
  const componentSearchClear = document.getElementById("component-search-clear");
  if (componentSearchBtn) {
    componentSearchBtn.onclick = () => {
      componentSearch = componentSearchInput?.value.trim() || "";
      render();
    };
  }
  if (componentSearchClear) {
    componentSearchClear.onclick = () => {
      componentSearch = "";
      render();
    };
  }
  if (componentSearchInput) {
    componentSearchInput.onkeydown = event => {
      if (event.key === "Enter") {
        event.preventDefault();
        componentSearch = componentSearchInput.value.trim();
        render();
      }
    };
  }

  const selAllCmp = document.getElementById("select-all-components");
  const selVisibleCmp = document.getElementById("select-visible-components");
  const clearCmp = document.getElementById("clear-components");
  if (selAllCmp) {
    selAllCmp.onclick = () => {
      components.forEach(entry => selectedComponents.add(componentName(entry)));
      render();
    };
  }
  if (selVisibleCmp) {
    selVisibleCmp.onclick = () => {
      filteredComponents().forEach(entry => selectedComponents.add(componentName(entry)));
      render();
    };
  }
  if (clearCmp) {
    clearCmp.onclick = () => {
      selectedComponents.clear();
      render();
    };
  }
  document.querySelectorAll("input[data-component]").forEach(box => {
    box.onchange = () => {
      const name = box.getAttribute("data-component");
      if (box.checked) selectedComponents.add(name);
      else selectedComponents.delete(name);
    };
  });

  const selAll = document.getElementById("select-all");
  const selVisible = document.getElementById("select-visible");
  const selNone = document.getElementById("select-none");
  if (selAll) selAll.onclick = () => { tables.forEach(t => selected.add(t)); render(); };
  if (selVisible) selVisible.onclick = () => { filteredTables().forEach(t => selected.add(t)); render(); };
  if (selNone) selNone.onclick = () => { selected.clear(); render(); };
  document.querySelectorAll("input[data-table]").forEach(box => {
    box.onchange = () => {
      const name = box.getAttribute("data-table");
      if (box.checked) selected.add(name);
      else selected.delete(name);
    };
  });
  document.getElementById("schema").onchange = () => {
    readForm();
    components = [];
    selectedComponents.clear();
    componentSearch = "";
    tables = [];
    selected.clear();
    tableSearch = "";
    render();
  };
  if (typeof mountSessionChrome === "function") mountSessionChrome("/db-sync");
}

async function testEndpoint(which) {
  error = "";
  info = "";
  readForm();
  render();
  try {
    const result = await api("/api/db-sync/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint(which === "source" ? "source" : "target") }),
    });
    info = `${which} OK — user ${result.sessionUser}, db ${result.dbName}, service ${result.serviceName}`;
  } catch (e) {
    error = e.message;
  }
  await refreshStatus();
  render();
}

async function loadSchemas() {
  error = "";
  info = "";
  readForm();
  try {
    const result = await api("/api/db-sync/schemas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint("source") }),
    });
    schemas = result.schemas || [];
    info = `Loaded ${schemas.length} schema(s) from source.`;
  } catch (e) {
    error = e.message;
  }
  render();
}

async function loadComponents() {
  error = "";
  info = "";
  readForm();
  if (!form.schema) {
    error = "Select a schema first";
    render();
    return;
  }
  try {
    const result = await api("/api/db-sync/components", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint("source"), schema: form.schema }),
    });
    components = result.components || [];
    selectedComponents = new Set(components.map(componentName));
    componentSearch = "";
    tables = [];
    selected = new Set();
    tableSearch = "";
    info = `${result.message || `Loaded ${components.length} component(s).`} All selected — uncheck ones to skip, then Load tables.`;
  } catch (e) {
    components = [];
    selectedComponents.clear();
    error = e.message;
  }
  render();
}

async function loadTables() {
  error = "";
  info = "";
  readForm();
  if (!form.schema) {
    error = "Select a schema first";
    render();
    return;
  }
  const chosen = [...selectedComponents];
  if (components.length && !chosen.length) {
    error = "Select at least one component. Uncheck modules to skip; leave checked the ones you need.";
    render();
    return;
  }
  try {
    const payload = { endpoint: endpoint("source"), schema: form.schema };
    if (chosen.length) payload.components = chosen;
    const result = await api("/api/db-sync/tables", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    tables = result.tables || [];
    selected = new Set();
    tableSearch = "";
    info = chosen.length
      ? `Loaded ${tables.length} table(s) from ${chosen.length} component(s).`
      : `Loaded ${tables.length} table(s) (all schema tables — components not loaded).`;
  } catch (e) {
    error = e.message;
  }
  render();
}

function selectedTables() {
  readForm();
  document.querySelectorAll("input[data-table]").forEach(box => {
    const name = box.getAttribute("data-table");
    if (box.checked) selected.add(name);
    else selected.delete(name);
  });
  return [...selected];
}

async function compareSelected() {
  error = "";
  info = "";
  const tableList = selectedTables();
  if (!tableList.length) {
    error = "Select at least one table";
    render();
    return;
  }
  try {
    const result = await api("/api/db-sync/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: endpoint("source"),
        target: endpoint("target"),
        schema: form.schema,
        tables: tableList,
      }),
    });
    status = { ...status, compare: result };
    info = "Compare complete.";
  } catch (e) {
    error = e.message;
  }
  render();
}

async function startSync(retryFailed) {
  error = "";
  info = "";
  const tableList = selectedTables();
  if (!retryFailed && !tableList.length) {
    error = "Select at least one table";
    render();
    return;
  }
  try {
    await api("/api/db-sync/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: endpoint("source"),
        target: endpoint("target"),
        schema: form.schema,
        tables: tableList,
        addMissingColumns: form.addMissingColumns,
        createMissingTables: form.createMissingTables,
        replaceData: retryFailed ? false : form.replaceData,
        retryFailed: !!retryFailed,
      }),
    });
    info = retryFailed
      ? "Retry started. Tables that already match are skipped."
      : "Sync started.";
    await refreshStatus();
  } catch (e) {
    error = e.message;
  }
  render();
}

async function stopSync() {
  try {
    await api("/api/db-sync/stop", { method: "POST" });
  } catch (e) {
    error = e.message;
  }
  await refreshStatus();
  render();
}

async function refreshStatus() {
  try {
    status = await api("/api/db-sync/status");
  } catch (_) {}
}

async function initialise() {
  try {
    if (typeof AuthPerms !== "undefined") await AuthPerms.load();
    await refreshStatus();
    render();
  } catch (e) {
    root.innerHTML = `<div class="app-frame">${nav()}<div class="main-panel"><div class="shell"><section class="card"><h1>DB Sync</h1><p class="error">${esc(e.message)}</p></section></div></div></div>`;
    if (typeof mountSessionChrome === "function") mountSessionChrome("/db-sync");
  }
}

setInterval(async () => {
  if (!status.running) return;
  await refreshStatus();
  render();
}, 1500);

initialise();
