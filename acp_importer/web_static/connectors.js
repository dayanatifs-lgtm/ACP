const root = document.getElementById("root");
const MASK = "********";
let connectors = [];
let environments = [];
let selectedName = "";
let form = blankForm();
let message = "";
let error = "";
let testing = false;
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
  return {
    name: "",
    connectorType: "IFS",
    base_url: "",
    token_url: "",
    client_id: "",
    client_secret: "",
    grant_type: "password",
    token_client_auth: "basic",
    scope: "",
    username: "",
    password: "",
    xsrf_token: "",
    acp_folder: "C:\\UpdaClones",
    verify_tls: false,
    use_env_proxy: false,
  };
}

function nav() {
  if (typeof AuthPerms !== "undefined") {
    return AuthPerms.sidebarHtml("/connectors");
  }
  return `<aside class="sidebar">
    <div class="sidebar-brand"><span class="sidebar-mark" aria-hidden="true"></span><span>ACP IMPORTER</span></div>
    <div class="sidebar-section">Tools</div>
    <nav class="sidebar-nav sidebar-nav-tools"><a class="active" href="/connectors">Connectors</a></nav>
    <div class="sidebar-footer"><div class="sidebar-user" id="sidebar-user"></div><button type="button" id="sign-out" class="secondary sidebar-signout">Sign out</button></div>
  </aside>`;
}

function readForm() {
  const get = id => document.getElementById(id)?.value ?? "";
  const checked = id => !!document.getElementById(id)?.checked;
  form = {
    name: get("conn-name").trim(),
    connectorType: get("conn-type") || "IFS",
    base_url: get("conn-base").trim(),
    token_url: get("conn-token").trim(),
    client_id: get("conn-client").trim(),
    client_secret: get("conn-secret"),
    grant_type: get("conn-grant") || "password",
    token_client_auth: get("conn-auth") || "basic",
    scope: get("conn-scope").trim(),
    username: get("conn-user").trim(),
    password: get("conn-password"),
    xsrf_token: get("conn-xsrf"),
    acp_folder: get("conn-folder").trim() || "C:\\UpdaClones",
    verify_tls: checked("conn-tls"),
    use_env_proxy: checked("conn-proxy"),
  };
  return form;
}

function loadConnector(name) {
  const row = connectors.find(item => item.name === name);
  selectedName = name || "";
  if (!row) {
    form = blankForm();
    message = "";
    error = "";
    render();
    return;
  }
  form = {
    name: row.name,
    connectorType: row.connectorType || "IFS",
    base_url: row.base_url || "",
    token_url: row.token_url || "",
    client_id: row.client_id || "",
    client_secret: row.client_secret || "",
    grant_type: row.grant_type || "password",
    token_client_auth: row.token_client_auth || "basic",
    scope: row.scope || "",
    username: row.username || "",
    password: row.password || "",
    xsrf_token: row.xsrf_token || "",
    acp_folder: row.acp_folder || "C:\\UpdaClones",
    verify_tls: !!row.verify_tls,
    use_env_proxy: !!row.use_env_proxy,
  };
  message = "";
  error = "";
  render();
}

function connectorList() {
  if (!connectors.length) return `<p class="help">No connectors yet. Create one to populate the IFS environment dropdown on Import and Clone.</p>`;
  return `<ul class="connector-list">${connectors.map(item => `
    <li>
      <button type="button" class="connector-item${item.name === selectedName ? " active" : ""}" data-name="${esc(item.name)}">
        <b>${esc(item.name)}</b>
        <small>${esc(item.connectorType || "IFS")} · ${esc(item.base_url || "No URL")}</small>
      </button>
    </li>`).join("")}</ul>`;
}

function render() {
  const canCreate = typeof AuthPerms === "undefined" || AuthPerms.hasFunction("connectors", "create");
  const canEdit = typeof AuthPerms === "undefined" || AuthPerms.hasFunction("connectors", "edit");
  const canDelete = typeof AuthPerms === "undefined" || AuthPerms.hasFunction("connectors", "delete");
  const canTest = typeof AuthPerms === "undefined" || AuthPerms.hasFunction("connectors", "test");
  const canSave = selectedName ? canEdit : canCreate;
  root.innerHTML = `<div class="app-frame">${nav()}<div class="main-panel"><div class="shell">
    <header>
      <p class="eyebrow">IFS ENVIRONMENT SETUP</p>
      <h1>Connectors</h1>
      <p class="subtitle">Save IFS connection details here. Tested connectors appear in the IFS environment dropdown on ACP Importer and ACP Deploy.</p>
    </header>
    <div class="connector-layout">
      <section class="card connector-side">
        <div class="section-heading"><h2>Saved connectors</h2><button id="new-connector" class="secondary" ${canCreate ? "" : "disabled"}>New</button></div>
        ${connectorList()}
        <p class="help">Dropdown environments: ${environments.map(esc).join(", ") || "none"}</p>
      </section>
      <section class="card settings connector-form">
        <h2>${selectedName ? `Edit ${esc(selectedName)}` : "New connector"}</h2>
        <label>Connector name <input id="conn-name" value="${esc(form.name)}" placeholder="Dev1"/></label>
        <label>Connector type <select id="conn-type"><option value="IFS" ${form.connectorType === "IFS" ? "selected" : ""}>IFS</option></select></label>
        <label>Projection base URL <input id="conn-base" value="${esc(form.base_url)}" placeholder="https://.../projection/v1"/></label>
        <label>Token URL <input id="conn-token" value="${esc(form.token_url)}" placeholder="https://.../openid-connect/token"/></label>
        <label>Client ID <input id="conn-client" value="${esc(form.client_id)}"/></label>
        <label>Client secret <input id="conn-secret" type="password" value="${esc(form.client_secret)}" placeholder="${selectedName ? "Leave masked to keep current" : ""}"/></label>
        <label>Grant type <select id="conn-grant"><option value="password" ${form.grant_type === "password" ? "selected" : ""}>password</option><option value="client_credentials" ${form.grant_type === "client_credentials" ? "selected" : ""}>client_credentials</option></select></label>
        <label>Token client auth <select id="conn-auth"><option value="basic" ${form.token_client_auth === "basic" ? "selected" : ""}>basic</option><option value="body" ${form.token_client_auth === "body" ? "selected" : ""}>body</option></select></label>
        <label>Username <input id="conn-user" value="${esc(form.username)}"/></label>
        <label>Password <input id="conn-password" type="password" value="${esc(form.password)}" placeholder="${selectedName ? "Leave masked to keep current" : ""}"/></label>
        <label>Scope <input id="conn-scope" value="${esc(form.scope)}"/></label>
        <label>XSRF token <input id="conn-xsrf" value="${esc(form.xsrf_token)}" placeholder="Optional"/></label>
        <label>Default ACP folder <input id="conn-folder" value="${esc(form.acp_folder)}"/></label>
        <label class="check-row"><input id="conn-tls" type="checkbox" ${form.verify_tls ? "checked" : ""}/> Verify TLS certificates</label>
        <label class="check-row"><input id="conn-proxy" type="checkbox" ${form.use_env_proxy ? "checked" : ""}/> Use system HTTP(S)_PROXY</label>
        ${error ? `<p class="error">${esc(error)}</p>` : ""}
        ${message ? `<p class="success">${esc(message)}</p>` : ""}
        <div class="buttons">
          <button id="test-connector" class="secondary" ${!canTest || testing || saving ? "disabled" : ""}>${testing ? "Testing…" : "Test Connection"}</button>
          <button id="save-connector" class="primary" ${!canSave || testing || saving ? "disabled" : ""}>${saving ? "Saving…" : "Save connector"}</button>
          <button id="delete-connector" class="danger" ${!canDelete || !selectedName || testing || saving ? "disabled" : ""}>Delete</button>
        </div>
      </section>
    </div>
  </div></div></div>`;

  document.getElementById("new-connector").onclick = () => loadConnector("");
  document.querySelectorAll(".connector-item").forEach(button => {
    button.onclick = () => loadConnector(button.getAttribute("data-name") || "");
  });
  ["conn-name", "conn-type", "conn-base", "conn-token", "conn-client", "conn-secret", "conn-grant", "conn-auth", "conn-user", "conn-password", "conn-scope", "conn-xsrf", "conn-folder"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { readForm(); });
    if (el) el.addEventListener("change", () => { readForm(); });
  });
  ["conn-tls", "conn-proxy"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => { readForm(); });
  });
  document.getElementById("test-connector").onclick = testConnection;
  document.getElementById("save-connector").onclick = saveConnector;
  document.getElementById("delete-connector").onclick = deleteSelected;
  if (typeof mountSessionChrome === "function") mountSessionChrome("/connectors");
}

async function refresh() {
  const data = await api("/api/connectors");
  connectors = data.connectors || [];
  environments = data.environments || [];
  if (selectedName && !connectors.some(item => item.name === selectedName)) {
    selectedName = "";
    form = blankForm();
  } else if (selectedName) {
    const row = connectors.find(item => item.name === selectedName);
    if (row) loadConnector(selectedName);
  }
  render();
}

async function testConnection() {
  error = "";
  message = "";
  const payload = { ...readForm(), existing_name: selectedName || null };
  testing = true;
  render();
  try {
    const result = await api("/api/connectors/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    message = result.message || "Connection successful.";
    environments = result.environments || environments;
  } catch (e) {
    error = e.message;
  } finally {
    testing = false;
    render();
  }
}

async function saveConnector() {
  error = "";
  message = "";
  const payload = { ...readForm(), rename_from: selectedName || null };
  if (!payload.name) {
    error = "Connector name is required";
    render();
    return;
  }
  if (!payload.base_url || !payload.token_url || !payload.client_id) {
    error = "Projection base URL, Token URL, and Client ID are required";
    render();
    return;
  }
  saving = true;
  render();
  try {
    const result = await api(`/api/connectors/${encodeURIComponent(payload.name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    connectors = (await api("/api/connectors")).connectors;
    environments = result.environments || environments;
    selectedName = result.connector.name;
    form = { ...form, ...result.connector, client_secret: result.connector.client_secret || MASK, password: result.connector.password || (result.connector.has_password ? MASK : ""), xsrf_token: result.connector.xsrf_token || "" };
    message = `Saved “${selectedName}”. It is now available in the IFS environment dropdown.`;
  } catch (e) {
    error = e.message;
  } finally {
    saving = false;
    render();
  }
}

async function deleteSelected() {
  if (!selectedName) return;
  if (!window.confirm(`Delete connector “${selectedName}”?`)) return;
  error = "";
  message = "";
  try {
    const result = await api(`/api/connectors/${encodeURIComponent(selectedName)}`, { method: "DELETE" });
    environments = result.environments || environments;
    selectedName = "";
    form = blankForm();
    await refresh();
    message = "Connector deleted.";
    render();
  } catch (e) {
    error = e.message;
    render();
  }
}

async function boot() {
  if (typeof AuthPerms !== "undefined") await AuthPerms.load();
  await refresh();
}
boot().catch(e => {
  root.innerHTML = `<div class="app-frame">${nav()}<div class="main-panel"><div class="shell"><section class="card"><h1>Connectors</h1><p class="error">${esc(e.message)}</p></section></div></div></div>`;
});
