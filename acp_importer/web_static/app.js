const h = React.createElement;

function formatBytes(bytes) { return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`; }

function publishStatus(message) {
  if (message.includes("Published successfully")) return { label: "PUBLISHED", style: "published" };
  if (message.includes("Publish skipped")) return { label: "ALREADY PUBLISHED / COMMAND HIDDEN", style: "skipped" };
  if (message.includes("Publish failed")) return { label: "PUBLISH FAILED", style: "publish-failed" };
  return { label: "PUBLISH STATUS NOT AVAILABLE", style: "unknown" };
}

function splitLog(message) {
  const marker = "; log: ";
  const index = message.indexOf(marker);
  return index === -1 ? { summary: message, log: "" } : { summary: message.slice(0, index), log: message.slice(index + marker.length) };
}

function Dashboard() {
  const [packages, setPackages] = React.useState([]);
  const [folder, setFolder] = React.useState("");
  const [workspacePath, setWorkspacePath] = React.useState("");
  const [workspaceFiles, setWorkspaceFiles] = React.useState([]);
  const [uploading, setUploading] = React.useState(false);
  const [environments, setEnvironments] = React.useState([]);
  const [environment, setEnvironment] = React.useState("");
  const [status, setStatus] = React.useState({ running: false, message: "Loading…", results: [] });
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");
  const fileRef = React.useRef(null);

  const refreshStatus = React.useCallback(async () => {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("Could not read importer status.");
    setStatus(await response.json());
  }, []);

  const refreshWorkspace = React.useCallback(async () => {
    const data = await Workspace.load();
    setWorkspacePath(data.path || "");
    setWorkspaceFiles(data.files || []);
    setFolder(data.token || "__workspace__");
    return data;
  }, []);

  const refreshPackages = React.useCallback(async (selectedEnvironment = environment) => {
    const params = new URLSearchParams({
      environment: selectedEnvironment,
      folder: Workspace.token() || "__workspace__",
    });
    const response = await fetch(`/api/packages?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not read packages.");
    setPackages(data.packages || []);
    if (data.folder) setWorkspacePath(data.folder);
  }, [environment]);

  React.useEffect(() => {
    (async () => {
      try {
        await refreshWorkspace();
        const response = await fetch("/api/environments");
        const data = await response.json();
        setEnvironments(data.environments);
        setEnvironment(data.default);
      } catch (err) { setError(err.message); }
    })();
  }, [refreshWorkspace]);

  React.useEffect(() => {
    if (!environment) return;
    refreshPackages().catch(err => setError(err.message));
  }, [environment, refreshPackages]);

  React.useEffect(() => {
    refreshStatus().catch(err => setError(err.message));
    const timer = window.setInterval(() => refreshStatus().catch(err => setError(err.message)), 1500);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  React.useEffect(() => { if (typeof mountSessionChrome === "function") mountSessionChrome("/"); }, []);

  async function uploadFiles(event) {
    const files = event.target.files;
    if (!files || !files.length) return;
    setError("");
    setMessage("");
    setUploading(true);
    try {
      const data = await Workspace.upload(files);
      setWorkspaceFiles(data.files || []);
      setWorkspacePath(data.path || workspacePath);
      setMessage(data.message || "Upload complete.");
      await refreshPackages();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeFile(name) {
    if (!window.confirm(`Remove ${name}?`)) return;
    setError("");
    try {
      const data = await Workspace.remove(name);
      setWorkspaceFiles(data.files || []);
      await refreshPackages();
    } catch (err) {
      setError(err.message);
    }
  }

  async function start(dryRun) {
    setError("");
    setMessage("");
    const response = await fetch("/api/imports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ environment, folder: Workspace.token() || "__workspace__", dry_run: dryRun }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) setError(data.detail || "Could not start the import.");
    refreshStatus().catch(() => {});
  }

  async function stop() {
    setError("");
    const response = await fetch("/api/imports/stop", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) setError(data.detail || "Could not stop the import.");
    refreshStatus().catch(() => {});
  }

  return h("div", { className: "app-frame" },
    h("aside", { className: "sidebar" },
      h("div", { className: "sidebar-brand" }, h("span", { className: "sidebar-mark", "aria-hidden": "true" }), h("span", null, "ACP IMPORTER")),
      h("div", { className: "sidebar-section" }, "Tools"),
      h("nav", { className: "sidebar-nav sidebar-nav-tools" },
        h("a", { className: "active", href: "/" }, "ACP Importer"),
        h("a", { href: "/clone" }, "ACP Clone"),
        h("a", { href: "/calendar" }, "Delivery Calendar"),
        h("a", { href: "/releases" }, "Jira Releases"),
        h("a", { href: "/connectors" }, "Connectors")
      ),
      h("div", { className: "sidebar-footer" },
        h("div", { className: "sidebar-user", id: "sidebar-user" }),
        h("button", { type: "button", id: "sign-out", className: "secondary sidebar-signout" }, "Sign out")
      )
    ),
    h("div", { className: "main-panel" },
      h("div", { className: "shell" },
        h("header", null,
          h("p", { className: "eyebrow" }, "IFS DELIVERY TOOL"),
          h("h1", null, "ACP Importer"),
          h("p", { className: "subtitle" }, "Upload packages from your computer into your private server workspace, then import to IFS.")
        ),
        h("section", { className: "card settings" },
          h("h2", null, "Import settings"),
          h("label", null, "IFS environment",
            h("select", { value: environment, disabled: status.running, onChange: event => setEnvironment(event.target.value) },
              environments.map(name => h("option", { key: name, value: name }, name))
            )
          ),
          h("div", { className: "workspace-panel" },
            h("div", { className: "workspace-path" },
              h("span", null, "Your server workspace"),
              h("code", null, workspacePath || "Loading…")
            ),
            h("p", { className: "help" },
              "Choose .acp / .zip files on your PC. They are stored on the server under your account so Import can run from any computer."
            ),
            h("div", { className: "workspace-upload path-row" },
              h("input", {
                ref: fileRef,
                type: "file",
                accept: ".acp,.zip,application/zip",
                multiple: true,
                disabled: status.running || uploading,
                onChange: uploadFiles,
              }),
              h("button", {
                className: "secondary",
                disabled: status.running || uploading,
                onClick: () => refreshPackages().catch(err => setError(err.message)),
              }, "Refresh")
            ),
            workspaceFiles.length
              ? h("ul", { className: "workspace-files" }, workspaceFiles.map(file =>
                  h("li", { key: file.name },
                    h("span", null, h("b", null, file.name), " ", h("small", null, formatBytes(file.bytes))),
                    h("button", {
                      type: "button",
                      className: "secondary",
                      disabled: status.running || uploading,
                      onClick: () => removeFile(file.name),
                    }, "Remove")
                  )
                ))
              : h("p", { className: "help" }, "No packages uploaded yet.")
          ),
          h("div", { className: "buttons" },
            h("button", { className: "secondary", disabled: status.running, onClick: () => start(true) }, "Dry run"),
            h("button", { className: "primary", disabled: status.running || packages.length === 0, onClick: () => start(false) }, "Run import"),
            h("button", { className: "danger", disabled: !status.running || status.cancel_requested, onClick: stop }, status.cancel_requested ? "Stopping…" : "Stop import")
          )
        ),
        error && h("p", { className: "error" }, error),
        message && h("p", { className: "success" }, message),
        h("section", { className: "card" }, h("h2", null, status.running ? "Import in progress" : "Current status"), h("p", { className: status.running ? "progress" : "" }, status.message)),
        h("section", { className: "card" }, h("h2", null, `Ready packages (${packages.length})`), packages.length ? h("ul", { className: "packages" }, packages.map(file => h("li", { key: file.name }, h("span", null, file.name), h("small", null, formatBytes(file.bytes))))) : h("p", null, "No .acp or .zip files found.")),
        h("section", { className: "card" }, h("h2", null, "Latest results"), status.results.length ? h("ul", { className: "results" }, status.results.map(item => {
          const publish = publishStatus(item.message);
          const content = splitLog(item.message);
          return h("li", { key: item.name },
            h("div", { className: "result-heading" },
              h("strong", { className: item.success ? "success" : "failed" }, item.success ? "IMPORT SUCCESS" : "IMPORT FAILED"),
              h("span", { className: `publish-badge ${publish.style}` }, publish.label),
              h("b", null, item.name)
            ),
            h("div", { className: "result-content" },
              h("p", null, content.summary),
              content.log ? h("details", null, h("summary", null, "Import log"), h("pre", { className: "import-log" }, content.log)) : null
            )
          );
        })) : h("p", null, "No imports have been run yet."))
      )
    )
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(h(Dashboard));
