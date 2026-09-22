const root = document.getElementById("root");
let status = { running: false, message: "Ready", results: [], compare: null, completed: 0, total: 0, phase: "idle" };
let schemas = [];
let tables = [];
let selected = new Set();
let error = "";
let info = "";

const form = {
  sourceHost: "10.242.66.100",
  sourcePort: "1521",
  sourceService: "thorcfg1_1",
  sourceConnectAs: "sid",
  sourceUser: "IFSINFO",
  sourcePassword: "",
  targetHost: "",
  targetPort: "1521",
  targetService: "",
  targetConnectAs: "sid",
  targetUser: "",
  targetPassword: "",
  thick: true,
  schema: "",
  addMissingColumns: true,
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
  form.replaceData = checked("opt-replace");
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
    <div class="items-table-wrap"><table class="items-table">
      <thead><tr><th>Table</th><th>Status</th><th>Missing columns on target</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></section>`;
}

function resultsBlock() {
  if (!status.results || !status.results.length) return "";
  const rows = status.results.map(r => `<li><b class="${r.success ? "success" : "failed"}">${r.success ? "OK" : "FAIL"}</b> <b>${esc(r.table)}</b><p>${esc(r.message)}</p></li>`).join("");
  return `<section class="card"><h2>Sync results</h2><ul class="results">${rows}</ul></section>`;
}

function render() {
  const schemaOptions = [`<option value="">Select schema…</option>`]
    .concat(schemas.map(s => `<option value="${esc(s)}" ${form.schema === s ? "selected" : ""}>${esc(s)}</option>`))
    .join("");
  const tableList = tables.length
    ? `<div class="packages" style="max-height:280px;overflow:auto">${tables.map(t => {
        const id = `tbl-${t}`;
        return `<label class="check-row"><input type="checkbox" id="${esc(id)}" data-table="${esc(t)}" ${selected.has(t) ? "checked" : ""}/> ${esc(t)}</label>`;
      }).join("")}</div>
      <div class="buttons" style="margin-top:10px">
        <button type="button" class="secondary" id="select-all" ${status.running ? "disabled" : ""}>Select all</button>
        <button type="button" class="secondary" id="select-none" ${status.running ? "disabled" : ""}>Clear</button>
      </div>`
    : `<p class="help">Load schemas, choose a schema, then load tables.</p>`;

  root.innerHTML = `<div class="app-frame">${nav()}<div class="main-panel"><div class="shell">
    <header>
      <p class="eyebrow">ORACLE CFG → DEV</p>
      <h1>DB Sync</h1>
      <p class="subtitle">Compare source/target tables, optionally add missing columns on DEV, then copy rows in FK-aware order. Passwords stay in memory only (not saved).</p>
    </header>
    <p class="help">This is a logical sync (python-oracledb), not OS Data Pump. Tables that do not exist on DEV must be created first. Prefer a small table list before full schema copy. DPY-4011 usually means Native Network Encryption — keep thick mode on and install Oracle Instant Client on the app server.</p>
    ${error ? `<p class="error">${esc(error)}</p>` : ""}
    ${info ? `<p class="success">${esc(info)}</p>` : ""}
    <section class="card settings">
      <h2>Client mode</h2>
      <label class="check-row"><input id="opt-thick" type="checkbox" ${form.thick ? "checked" : ""}/> Use Oracle Instant Client (thick mode) — required when DB uses network encryption</label>
      <p class="help">Set server env <code>ORACLE_CLIENT_LIB_DIR</code> to the folder with <code>oci.dll</code>, then restart. Current: ${esc((status.thick && status.thick.libDir) || "not initialized")}${status.thick && status.thick.error ? ` — ${esc(status.thick.error)}` : ""}</p>
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
        <button type="button" class="secondary" id="load-tables" ${status.running || !form.schema ? "disabled" : ""}>Load tables</button>
        <button type="button" class="secondary" id="compare" ${status.running || !selected.size ? "disabled" : ""}>Compare selected</button>
      </div>
      ${tableList}
      <label class="check-row"><input id="opt-add-cols" type="checkbox" ${form.addMissingColumns ? "checked" : ""}/> Add missing columns on DEV (nullable)</label>
      <label class="check-row"><input id="opt-replace" type="checkbox" ${form.replaceData ? "checked" : ""}/> Replace target rows (DELETE then INSERT)</label>
      <div class="buttons">
        <button type="button" class="primary" id="start-sync" ${status.running || !selected.size ? "disabled" : ""}>${status.running ? "Syncing…" : "Start sync"}</button>
        <button type="button" class="danger" id="stop-sync" ${!status.running ? "disabled" : ""}>Stop</button>
      </div>
    </section>
    <section class="card"><h2>Status</h2><p class="${status.running ? "progress" : ""}">${esc(status.message)}${status.total ? ` (${status.completed}/${status.total})` : ""}</p></section>
    ${compareBlock()}
    ${resultsBlock()}
  </div></div></div>`;

  document.querySelectorAll("[data-test]").forEach(btn => {
    btn.onclick = () => testEndpoint(btn.getAttribute("data-test"));
  });
  document.getElementById("load-schemas").onclick = loadSchemas;
  document.getElementById("load-tables").onclick = loadTables;
  document.getElementById("compare").onclick = compareSelected;
  document.getElementById("start-sync").onclick = startSync;
  document.getElementById("stop-sync").onclick = stopSync;
  const selAll = document.getElementById("select-all");
  const selNone = document.getElementById("select-none");
  if (selAll) selAll.onclick = () => { tables.forEach(t => selected.add(t)); render(); };
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
    tables = [];
    selected.clear();
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

async function loadTables() {
  error = "";
  info = "";
  readForm();
  if (!form.schema) {
    error = "Select a schema first";
    render();
    return;
  }
  try {
    const result = await api("/api/db-sync/tables", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint("source"), schema: form.schema }),
    });
    tables = result.tables || [];
    selected = new Set();
    info = `Loaded ${tables.length} table(s).`;
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

async function startSync() {
  error = "";
  info = "";
  const tableList = selectedTables();
  if (!tableList.length) {
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
        replaceData: form.replaceData,
      }),
    });
    info = "Sync started.";
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
