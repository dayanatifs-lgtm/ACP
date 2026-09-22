const root = document.getElementById("root");
let environments = [], analysis = null, status = { running: false, results: [], message: "Ready" }, analysisRequested = false;
let cloneFolder = "__workspace__";
let cloneEnvironment = "";
let mode = "direct";
let aiConfigured = false;
let repackSource = "__workspace__";
let repackOutput = "";
let repackMax = 10;
let repackReport = null, repackBuildRequested = false, repackFilter = { category: "", status: "" };
let repackStatus = { running: false, results: [], message: "Ready to repackage", phase: "idle" };
let workspaceMessage = "";
function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]); }
async function api(url, options) { const response = await fetch(url, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Request failed"); return data; }
function env() { return document.getElementById("clone-environment")?.value || cloneEnvironment; }
function folder() { return (typeof Workspace !== "undefined" && Workspace.token()) || cloneFolder || "__workspace__"; }
function readRepackSettings() {
  const max = document.getElementById("repack-max");
  if (max) repackMax = Number(max.value || 10);
  repackSource = folder();
  repackOutput = (typeof Workspace !== "undefined" && Workspace.repackageOutput()) || repackOutput;
}
function nav() {
  if (typeof AuthPerms !== "undefined") return AuthPerms.sidebarHtml("/clone");
  return `<aside class="sidebar">
    <div class="sidebar-brand"><span class="sidebar-mark" aria-hidden="true"></span><span>ACP IMPORTER</span></div>
    <div class="sidebar-section">Tools</div>
    <nav class="sidebar-nav sidebar-nav-tools"><a class="active" href="/clone">ACP Clone</a></nav>
    <div class="sidebar-footer"><div class="sidebar-user" id="sidebar-user"></div><button type="button" id="sign-out" class="secondary sidebar-signout">Sign out</button></div>
  </aside>`;
}
function page(content) {
  return `<div class="app-frame">${nav()}<div class="main-panel">${content}</div></div>`;
}
function modeButtons() {
  return `<div class="mode-toggle"><button class="${mode === "direct" ? "primary" : "secondary"}" id="mode-direct">Direct ACP Clone</button><button class="${mode === "repackage" ? "primary" : "secondary"}" id="mode-repack">Repackage &amp; Deploy</button></div>`;
}
function summary() {
  const s = analysis.summary || {};
  return `<div class="stats-grid">
    ${[["ACPs scanned", s.acpsScanned],["Objects discovered", s.objectsDiscovered],["Dependencies found", s.dependenciesFound],["Resolved", s.resolved],["Ambiguous", s.ambiguous],["Missing", s.missing],["Circular", s.circular]].map(([label, value]) => `<div class="stat"><b>${esc(value ?? 0)}</b><span>${label}</span></div>`).join("")}
  </div>`;
}
function levels() {
  const plan = analysis.deploymentPlan || [];
  if (!plan.length) return "<p class=\"help\">No deployment levels. Circular chains are excluded until resolved.</p>";
  return `<div class="level-list">${plan.map(level => `<div class="level-row"><b>Level ${level.level}</b><span>${level.count} ACP${level.count === 1 ? "" : "s"}: ${esc(level.packages.map(p => p.name).join(", "))}</span></div>`).join("")}</div>`;
}
function edgeRows(items, empty) {
  if (!items || !items.length) return `<p class="help">${empty}</p>`;
  return `<div class="items-table-wrap"><table class="items-table"><thead><tr><th>Consumer</th><th>Provider</th><th>Object</th><th>Type</th><th>Confidence</th><th>Detection</th></tr></thead><tbody>${items.map(edge => `<tr><td>${esc(edge.consumerAcp || edge.consumerItem)}</td><td>${esc(edge.providerAcp || edge.providerItem || (edge.candidates || []).join(", ") || "—")}</td><td>${esc(edge.objectName || edge.referencedObject)}<p class="edge-evidence">${esc((edge.evidence || []).join(" · "))}</p></td><td>${esc(edge.dependencyType)}</td><td>${esc(edge.status === "CONFIRMED" ? edge.confidence : edge.status)}</td><td>${esc(edge.detectionMethod)}</td></tr>`).join("")}</tbody></table></div>`;
}
function analysisCard() {
  if (!analysis) return "";
  const warning = (analysis.duplicates.length || analysis.missing.length || analysis.cycles.length || analysis.invalid.length || analysis.blockedPackages?.length)
    ? `Duplicates (only the first copy is indexed): ${analysis.duplicates.join(", ") || "none"}; Missing: ${analysis.missing.map(x => `${x.package} → ${x.dependency}`).join(", ") || "none"}; Cycles: ${analysis.cycles.join("; ") || "none"}; Blocked packages: ${(analysis.blockedPackages || []).map(x => x.name).join(", ") || "none"}; Invalid archives: ${analysis.invalid.map(x => x.file).join(", ") || "none"}`
    : "";
  const rows = analysis.packages.map(x => `<tr><td>${x.order}</td><td>L${x.level}</td><td><b>${esc(x.name)}</b><br><small>${esc(x.file)}</small>${x.itemTypes?.length ? `<br><small>${esc(x.itemTypes.join(", "))}</small>` : ""}</td><td>${esc(x.dependencies.map(d => `${d.name} (${d.confidence})`).join(", ") || "None")}</td><td>${x.cyclic ? "CYCLE" : esc(x.confidence)}</td></tr>`).join("");
  return `<section class="card"><div class="section-heading"><h2>Dependency analysis</h2><div class="buttons"><a class="secondary button-link" href="/api/clone/analysis/export?format=json">Export JSON</a><a class="secondary button-link" href="/api/clone/analysis/export?format=csv">Export CSV</a></div></div>
    <p class="help">${esc(analysis.notes)}</p>${warning ? `<p class="error">Review before starting. ${esc(warning)}</p>` : ""}
    ${summary()}<h3>Deployment plan</h3>${levels()}
    <h3>Resolved dependencies</h3>${edgeRows((analysis.edges || []).filter(e => e.status === "CONFIRMED"), "No confirmed package-to-package dependencies.")}
    <h3>Ambiguous providers</h3>${edgeRows(analysis.ambiguousDependencies, "No ambiguous providers.")}
    <h3>Missing providers</h3>${edgeRows(analysis.missingDependencies, "No missing providers.")}
    <h3>Deployment order</h3>
    <div class="items-table-wrap"><table class="items-table"><thead><tr><th>#</th><th>Level</th><th>ACP</th><th>Dependencies</th><th>Confidence</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}
function bindMode() {
  document.getElementById("mode-direct").onclick = () => { mode = "direct"; render(); };
  document.getElementById("mode-repack").onclick = () => { mode = "repackage"; render(); };
}
function renderDirect(error) {
  const options = environments.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  const results = status.results.length ? status.results.map(x => `<li><b class="${x.success ? "success" : "failed"}">${x.skipped ? "SKIPPED" : x.ai ? (x.success ? "AI SUCCESS" : "AI FAILED") : x.success ? "SUCCESS" : "FAILED"}</b> <b>${esc(x.name)}</b><p>${esc(x.message)}</p></li>`).join("") : "<p>No clone has been run yet.</p>";
  const progress = (status.phase === "analysing" || status.phase === "ai_retry") && status.total ? ` (${status.completed} / ${status.total})` : "";
  const stopLabel = status.cancel_requested ? "Stopping…" : status.phase === "analysing" ? "Stop analysis" : status.phase === "ai_retry" ? "Stop AI retry" : "Stop clone";
  const failedCount = (status.results || []).filter(x => !x.success && !x.ai).length;
  const canAiRetry = aiConfigured && failedCount > 0 && !status.running;
  const resultsExport = status.results.length ? `<div class="buttons"><a class="secondary button-link" href="/api/clone/results/export?format=json">Export JSON</a><a class="secondary button-link" href="/api/clone/results/export?format=csv">Export CSV</a></div>` : "";
  root.innerHTML = page(`<div class="shell clone-shell"><header><p class="eyebrow">DEPENDENCY-AWARE DEPLOYMENT</p><h1>ACP Clone</h1><p class="subtitle">Upload packages from your computer into your private server workspace, then analyse and clone.</p></header>
    <section class="card settings"><h2>Clone settings</h2>${modeButtons()}
    <label>IFS environment <select id="clone-environment" ${status.running ? "disabled" : ""}>${options}</select></label>
    ${typeof Workspace !== "undefined" ? Workspace.panelHtml({ disabled: status.running, inputId: "clone-workspace-files" }) : `<label>ACP folder path <input id="clone-folder" value="${esc(cloneFolder)}" ${status.running ? "disabled" : ""}/></label>`}
    ${workspaceMessage ? `<p class="success">${esc(workspaceMessage)}</p>` : ""}
    <p class="help">Files stay in your private folder on the server. Manage IFS environments under Connectors. After a clone run, Redeploy with AI retries failed ACPs using Gemini.</p>
    <div class="buttons"><button id="analyse" class="secondary" ${status.running ? "disabled" : ""}>${status.phase === "analysing" ? "Analysing…" : "Analyse dependencies"}</button><button id="start" class="primary" ${!analysis || !analysis.canStart || status.running ? "disabled" : ""}>Start ACP Clone</button><button id="ai-retry" class="secondary" ${canAiRetry ? "" : "disabled"}>${status.phase === "ai_retry" ? "AI retry…" : "Redeploy with AI"}</button><button id="stop" class="danger" ${!status.running || status.cancel_requested ? "disabled" : ""}>${stopLabel}</button></div>
    ${!aiConfigured ? `<p class="help">Redeploy with AI needs GEMINI_API_KEY in .env.</p>` : failedCount ? `<p class="help">${failedCount} failed ACP${failedCount === 1 ? "" : "s"} can be retried with Gemini.</p>` : ""}</section>
    ${error ? `<p class="error">${esc(error)}</p>` : ""}
    <section class="card"><h2>${status.phase === "analysing" ? "Dependency analysis in progress" : status.phase === "ai_retry" ? "AI retry in progress" : status.running ? "Clone in progress" : "Clone status"}</h2><p class="${status.running ? "progress" : ""}">${esc(status.message)}${progress}</p></section>
    ${analysisCard()}<section class="card"><div class="section-heading"><h2>Clone results</h2>${resultsExport}</div><ul class="results">${results}</ul></section></div>`);
  bindMode();
  document.getElementById("clone-environment").value = cloneEnvironment || environments[0] || "";
  document.getElementById("clone-environment").onchange = e => { cloneEnvironment = e.target.value; };
  if (typeof Workspace !== "undefined") {
    Workspace.bindPanel({
      inputId: "clone-workspace-files",
      disabled: status.running,
      onChange: async (info, err) => {
        workspaceMessage = err ? "" : (info?.message || "Workspace updated.");
        if (err) render(err.message);
        else render();
      },
    });
  } else {
    document.getElementById("clone-folder").oninput = e => { cloneFolder = e.target.value; };
  }
  document.getElementById("analyse").onclick = analysePackages; document.getElementById("start").onclick = startClone; document.getElementById("stop").onclick = stopClone;
  document.getElementById("ai-retry").onclick = startAiRetry;
  if (typeof mountSessionChrome === "function") mountSessionChrome("/clone");
}
function repackSummary() {
  const s = (repackReport && repackReport.summary) || {};
  return `<div class="stats-grid">${[["ACPs scanned", s.acpsScanned],["Items discovered", s.itemsDiscovered],["Dependencies", s.dependenciesDetected],["Confirmed", s.confirmed],["Ambiguous", s.ambiguous],["Missing", s.missing],["Circular", s.circular],["Generated packages", s.generatedPackages]].map(([label, value]) => `<div class="stat"><b>${esc(value ?? 0)}</b><span>${label}</span></div>`).join("")}</div>
    <p><b>Deployment ready:</b> ${s.deploymentReady ? "YES" : "NO"}</p>`;
}
function repackCategories() {
  if (!repackReport) return "";
  const busy = repackStatus.running;
  return `<div class="items-table-wrap"><table class="items-table"><thead><tr><th>Category</th><th>Original items</th><th>Generated packages</th><th>Ready</th><th></th></tr></thead><tbody>
    ${(repackReport.categories || []).map(cat => `<tr><td><b>${esc(cat.name)}</b></td><td>${cat.originalItems ?? cat.items}</td><td>${cat.packageCount}</td><td>${cat.ready ?? cat.packageCount}</td>
      <td><button class="secondary cat-import" data-category="${esc(cat.name)}" ${busy ? "disabled" : ""}>Import</button></td></tr>`).join("")}
  </tbody></table></div>`;
}
function filteredPackages() {
  return (repackReport.packages || []).filter(pkg => (!repackFilter.category || pkg.category === repackFilter.category) && (!repackFilter.status || (pkg.importStatus || pkg.status) === repackFilter.status));
}
function filteredEdges() {
  return (repackReport.edges || []).filter(edge => {
    if (repackFilter.status && edge.status !== repackFilter.status && !["MISSING","AMBIGUOUS","CONFIRMED"].includes(repackFilter.status)) return true;
    if (repackFilter.status && ["MISSING","AMBIGUOUS","CONFIRMED"].includes(repackFilter.status) && edge.status !== repackFilter.status) return false;
    if (!repackFilter.category) return true;
    return String(edge.consumerItem || "").includes(`:${repackFilter.category}:`) || String(edge.providerItem || "").includes(`:${repackFilter.category}:`);
  });
}
function repackPackages() {
  if (!repackReport) return "";
  const cats = [...new Set((repackReport.packages || []).map(p => p.category))];
  return `<label>Filter category <select id="repack-filter-cat"><option value="">All</option>${cats.map(c => `<option value="${esc(c)}" ${repackFilter.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
    <label>Filter status <select id="repack-filter-status"><option value="">All</option>${["READY","INVALID","PENDING","SUCCESS","FAILED","BLOCKED","CONFIRMED","AMBIGUOUS","MISSING"].map(s => `<option value="${s}" ${repackFilter.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
    <div class="items-table-wrap"><table class="items-table"><thead><tr><th>#</th><th>Package</th><th>Category</th><th>Items</th><th>Depends on</th><th>Validation</th><th>Import</th></tr></thead><tbody>
    ${filteredPackages().map(pkg => `<tr><td>${pkg.sequence}</td><td><b>${esc(pkg.package)}</b><div class="pkg-details">Source ACPs: ${esc((pkg.sourceAcps || []).join(", "))}<br>Items: ${esc((pkg.items || []).map(i => `${i.type}:${i.name}`).join(", "))}<br>Path: ${esc(pkg.path)}</div></td><td>${esc(pkg.category)}</td><td>${(pkg.items || []).length}</td><td>${esc((pkg.dependsOnPackages || []).join(", ") || "None")}</td><td>${pkg.validation?.ok ? "PASS" : "FAIL"}</td><td>${esc(pkg.importStatus || "PENDING")}</td></tr>`).join("")}
    </tbody></table></div>`;
}
function renderRepackage(error) {
  const options = environments.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  const busy = repackStatus.running;
  const results = (repackStatus.results || []).length ? repackStatus.results.map(x => `<li><b class="${x.success ? "success" : "failed"}">${x.skipped ? "SKIPPED" : x.success ? "SUCCESS" : "FAILED"}</b> <b>${esc(x.name)}</b><p>${esc(x.message)}</p>${x.sourceAcps ? `<p>Source ACPs: ${esc((x.sourceAcps || []).join(", "))}</p>` : ""}${x.items ? `<p>Items: ${esc((x.items || []).map(i => i.name || i).join(", "))}</p>` : ""}</li>`).join("") : "<p>No generated packages have been imported yet.</p>";
  const counts = (repackReport && repackReport.packages) ? {
    generated: repackReport.packages.length,
    imported: repackReport.packages.filter(p => p.importStatus === "SUCCESS").length,
    failed: repackReport.packages.filter(p => p.importStatus === "FAILED").length,
    pending: repackReport.packages.filter(p => !p.importStatus || p.importStatus === "PENDING").length,
    blocked: repackReport.packages.filter(p => p.importStatus === "BLOCKED").length
  } : null;
  root.innerHTML = page(`<div class="shell clone-shell"><header><p class="eyebrow">REPACKAGE &amp; DEPLOY</p><h1>ACP Clone</h1><p class="subtitle">Scan packages in your workspace, generate smaller valid packages, then import by category.</p></header>
    <section class="card settings"><h2>Repackage settings</h2>${modeButtons()}
      <label>IFS environment <select id="clone-environment" ${busy ? "disabled" : ""}>${options}</select></label>
      ${typeof Workspace !== "undefined" ? Workspace.panelHtml({ disabled: busy, inputId: "repack-workspace-files" }) : `<label>Source ACP folder <input id="repack-source" value="${esc(repackSource)}" ${busy ? "disabled" : ""}/></label>`}
      <div class="workspace-path"><span>Repackaged output</span><code>${esc(repackOutput || (typeof Workspace !== "undefined" ? Workspace.repackageOutput() : ""))}</code></div>
      <label>Maximum items per generated ACP <input id="repack-max" type="number" min="1" value="${esc(repackMax)}" ${busy ? "disabled" : ""}/></label>
      <p class="help">Analyse &amp; Build writes new ACPs to your workspace\\repackaged folder. Uploaded source files are never modified.</p>
      <div class="buttons">
        <button id="repack-build" class="secondary" ${busy ? "disabled" : ""}>${repackStatus.phase === "building" ? "Building…" : "Analyse &amp; Build Packages"}</button>
        <button id="repack-validate" class="secondary" ${busy || !repackReport ? "disabled" : ""}>Validate Packages</button>
        <button id="repack-import-all" class="primary" ${busy || !repackReport ? "disabled" : ""}>Import All</button>
        <button id="repack-stop" class="danger" ${!busy || repackStatus.cancel_requested ? "disabled" : ""}>Stop Import</button>
      </div>
    </section>
    ${error ? `<p class="error">${esc(error)}</p>` : ""}
    <section class="card"><h2>${busy ? "Repackage in progress" : "Repackage status"}</h2><p class="${busy ? "progress" : ""}">${esc(repackStatus.message)}${repackStatus.total ? ` (${repackStatus.completed} / ${repackStatus.total})` : ""}</p></section>
    ${repackReport ? `<section class="card"><h2>Analysis</h2>${repackSummary()}${(repackReport.warnings || []).map(w => `<p class="error">${esc(w)}</p>`).join("")}</section>
      <section class="card"><h2>Generated packages</h2>${repackCategories()}<div class="buttons" style="margin-top:12px"><button id="repack-import-all-2" class="primary" ${busy ? "disabled" : ""}>Import All</button></div></section>
      <section class="card"><h2>Dependency graph</h2>${edgeRows(filteredEdges(), "No item dependencies were detected.")}</section>
      <section class="card"><h2>Package contents</h2>${repackPackages()}</section>` : ""}
    <section class="card"><h2>Import status</h2>${counts ? `<p>Generated packages: ${counts.generated}. Imported: ${counts.imported}. Failed: ${counts.failed}. Pending: ${counts.pending}. Blocked: ${counts.blocked}.</p>` : ""}<ul class="results">${results}</ul></section></div>`);
  bindMode();
  document.getElementById("clone-environment").value = cloneEnvironment || environments[0] || "";
  document.getElementById("clone-environment").onchange = e => { cloneEnvironment = e.target.value; };
  if (typeof Workspace !== "undefined") {
    Workspace.bindPanel({ inputId: "repack-workspace-files", disabled: busy, onChange: async () => render() });
  } else {
    document.getElementById("repack-source").oninput = e => { repackSource = e.target.value; };
    document.getElementById("repack-output").oninput = e => { repackOutput = e.target.value; };
  }
  document.getElementById("repack-max").oninput = e => { repackMax = e.target.value; };
  document.getElementById("repack-build").onclick = buildRepack;
  document.getElementById("repack-validate").onclick = validateRepack;
  document.getElementById("repack-import-all").onclick = () => importRepack(null);
  document.getElementById("repack-stop").onclick = stopClone;
  const second = document.getElementById("repack-import-all-2");
  if (second) second.onclick = () => importRepack(null);
  document.querySelectorAll(".cat-import").forEach(button => { button.onclick = () => importRepack(button.dataset.category); });
  const cat = document.getElementById("repack-filter-cat");
  const st = document.getElementById("repack-filter-status");
  if (cat) cat.onchange = e => { repackFilter.category = e.target.value; render(); };
  if (st) st.onchange = e => { repackFilter.status = e.target.value; render(); };
  if (typeof mountSessionChrome === "function") mountSessionChrome("/clone");
}
function render(error = "") {
  if (mode === "repackage") renderRepackage(error);
  else renderDirect(error);
}
async function analysePackages() { try { cloneEnvironment = env(); cloneFolder = folder(); analysis = null; analysisRequested = true; await api(`/api/clone/analyse?${new URLSearchParams({environment:cloneEnvironment,folder:cloneFolder})}`, {method:"POST"}); status = await api("/api/clone/status"); render(); } catch (e) { render(e.message); } }
async function startClone() {
  try {
    const s = analysis.summary || {};
    if (s.ambiguous || s.missing || s.circular) {
      const ok = window.confirm(`Analysis has ${s.ambiguous || 0} ambiguous, ${s.missing || 0} missing, and ${s.circular || 0} circular result(s). Every valid ACP will still be imported; circular packages use a best-effort order. Continue?`);
      if (!ok) return;
    }
    await api("/api/clone/imports", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({environment:env(),folder:folder(),order:analysis.packages.map(x=>x.file)})});
  } catch (e) { render(e.message); }
}
async function stopClone() { try { await api("/api/imports/stop", {method:"POST"}); } catch (e) { render(e.message); } }
async function startAiRetry() {
  try {
    cloneEnvironment = env();
    cloneFolder = folder();
    await api("/api/clone/ai-retry", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({environment: cloneEnvironment, folder: cloneFolder, order: []})});
    status = await api("/api/clone/status");
    render();
  } catch (e) { render(e.message); }
}
async function buildRepack() {
  try {
    cloneEnvironment = env();
    readRepackSettings();
    repackReport = null;
    repackBuildRequested = true;
    await api("/api/repackage/build", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({source_folder: repackSource, output_folder: repackOutput, max_items: repackMax})});
    repackStatus = await api("/api/repackage/status");
    render();
  } catch (e) { render(e.message); }
}
async function validateRepack() {
  try {
    readRepackSettings();
    const data = await api(`/api/repackage/validate?${new URLSearchParams({output_folder: repackOutput})}`, {method:"POST"});
    repackReport = data.manifest;
    if (repackReport?.outputFolder) repackOutput = repackReport.outputFolder;
    render(data.ok ? "" : "One or more generated packages failed validation.");
  } catch (e) { render(e.message); }
}
async function importRepack(category) {
  try {
    readRepackSettings();
    if (category) {
      const ok = window.confirm(`Import only ${category} packages? Packages whose dependencies are not imported yet will be blocked.`);
      if (!ok) return;
    }
    await api("/api/repackage/imports", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({environment: env(), output_folder: repackOutput, category: category || null})});
    repackStatus = await api("/api/repackage/status");
    render();
  } catch (e) { render(e.message); }
}
async function initialise() {
  try {
    if (typeof AuthPerms !== "undefined") await AuthPerms.load();
    if (typeof Workspace !== "undefined") {
      const ws = await Workspace.load();
      cloneFolder = ws.token || "__workspace__";
      repackSource = ws.token || "__workspace__";
      repackOutput = ws.repackageOutput || "";
    }
    const [envData, cloneStatus, packStatus, aiStatus] = await Promise.all([
      api("/api/environments").catch(() => ({ environments: [], default: "" })),
      api("/api/clone/status").catch(() => status),
      api("/api/repackage/status").catch(() => repackStatus),
      api("/api/clone/ai/status").catch(() => ({ configured: false })),
    ]);
    environments = envData.environments || [];
    cloneEnvironment = envData.default || "";
    status = cloneStatus;
    repackStatus = packStatus;
    aiConfigured = !!aiStatus.configured;
    render();
  } catch (e) {
    root.innerHTML = page(`<div class="shell"><section class="card"><h1>ACP Clone</h1><p class="error">${esc(e.message)}</p></section></div>`);
    if (typeof mountSessionChrome === "function") mountSessionChrome("/clone");
  }
}
setInterval(async()=>{ try {
  if (mode === "repackage") {
    repackStatus = await api("/api/repackage/status");
    if (repackBuildRequested && !repackStatus.running) {
      if (repackStatus.folder) repackOutput = repackStatus.folder;
      try { repackReport = await api(`/api/repackage/report?${new URLSearchParams({output_folder: repackOutput})}`); } catch (_) { repackReport = null; }
      if (repackReport?.outputFolder) repackOutput = repackReport.outputFolder;
      repackBuildRequested = false;
    }
    const focusedId = document.activeElement?.id;
    if (!repackStatus.running && ["repack-source","repack-output","repack-max","clone-environment"].includes(focusedId)) return;
    render();
    return;
  }
  status=await api("/api/clone/status");
  if (!aiConfigured) {
    try { aiConfigured = !!(await api("/api/clone/ai/status")).configured; } catch (_) {}
  }
  if (analysisRequested && !status.running) { try { analysis=await api("/api/clone/analysis"); analysisRequested=false; } catch (_) {} }
  const focusedId = document.activeElement?.id;
  if (!status.running && (focusedId === "clone-environment" || focusedId === "clone-workspace-files" || focusedId === "repack-workspace-files" || focusedId === "repack-max")) return;
  render();
} catch (_) {} }, 1500);
initialise();
