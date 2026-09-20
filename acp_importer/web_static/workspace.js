/** Shared per-user server workspace helpers (upload ACP packages). */
const Workspace = (() => {
  let info = null;

  function formatBytes(bytes) {
    const n = Number(bytes) || 0;
    return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
  }

  async function load() {
    const response = await fetch("/api/workspace");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Could not load workspace.");
    info = data;
    return data;
  }

  function getInfo() {
    return info;
  }

  function path() {
    return (info && info.path) || "";
  }

  function token() {
    return (info && info.token) || "__workspace__";
  }

  function repackageOutput() {
    return (info && info.repackageOutput) || "";
  }

  async function upload(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) throw new Error("Choose one or more .acp / .zip files.");
    const body = new FormData();
    files.forEach(file => body.append("files", file, file.name));
    const response = await fetch("/api/workspace/upload", { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Upload failed.");
    info = data;
    return data;
  }

  async function remove(filename) {
    const response = await fetch(`/api/workspace/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Could not delete file.");
    info = data;
    return data;
  }

  function panelHtml({ disabled = false, inputId = "workspace-files" } = {}) {
    const files = (info && info.files) || [];
    const list = files.length
      ? `<ul class="workspace-files">${files.map(f => `
          <li>
            <span><b>${escapeHtml(f.name)}</b> <small>${formatBytes(f.bytes)}</small></span>
            <button type="button" class="secondary workspace-delete" data-name="${escapeHtml(f.name)}" ${disabled ? "disabled" : ""}>Remove</button>
          </li>`).join("")}</ul>`
      : `<p class="help">No packages uploaded yet.</p>`;
    return `<div class="workspace-panel">
      <div class="workspace-path"><span>Your server workspace</span><code>${escapeHtml(path())}</code></div>
      <p class="help">Upload .acp / .zip files from your computer. The server stores them in your private folder and Import/Clone use that folder.</p>
      <div class="workspace-upload path-row">
        <input id="${inputId}" type="file" accept=".acp,.zip,application/zip" multiple ${disabled ? "disabled" : ""}/>
        <button type="button" class="primary" id="${inputId}-upload" ${disabled ? "disabled" : ""}>Upload</button>
      </div>
      ${list}
    </div>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  }

  function bindPanel({ inputId = "workspace-files", onChange, disabled = false } = {}) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(`${inputId}-upload`);
    if (button) {
      button.onclick = async () => {
        try {
          await upload(input?.files);
          if (input) input.value = "";
          if (onChange) await onChange(info);
        } catch (err) {
          if (onChange) await onChange(info, err);
          else alert(err.message);
        }
      };
    }
    document.querySelectorAll(".workspace-delete").forEach(btn => {
      btn.onclick = async () => {
        if (!confirm(`Remove ${btn.dataset.name}?`)) return;
        try {
          await remove(btn.dataset.name);
          if (onChange) await onChange(info);
        } catch (err) {
          if (onChange) await onChange(info, err);
          else alert(err.message);
        }
      };
    });
    void disabled;
  }

  return { load, getInfo, path, token, repackageOutput, upload, remove, panelHtml, bindPanel, formatBytes };
})();
