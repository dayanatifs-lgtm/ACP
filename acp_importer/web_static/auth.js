const AuthPage = (() => {
  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Request failed");
    return data;
  }

  function show(el, text, isError) {
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    if (isError != null) el.className = isError ? "error" : "success";
  }

  function bindPasswordToggle(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    if (!input || !button) return;
    button.addEventListener("click", () => {
      const showPassword = input.type === "password";
      input.type = showPassword ? "text" : "password";
      button.setAttribute("aria-label", showPassword ? "Hide password" : "Show password");
    });
  }

  function bindLogin() {
    const form = document.getElementById("login-form");
    const error = document.getElementById("error");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      show(error, "", true);
      try {
        await api("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: document.getElementById("email").value.trim(),
            password: document.getElementById("password").value,
          }),
        });
        const next = new URLSearchParams(location.search).get("next");
        if (next && next.startsWith("/") && !next.startsWith("//")) {
          location.href = next;
          return;
        }
        try {
          const status = await api("/api/auth/status");
          const pages = (status.permissions && status.permissions.pages) || {};
          const order = [
            ["dashboard", "/"],
            ["clone", "/clone"],
            ["calendar", "/calendar"],
            ["releases", "/releases"],
            ["connectors", "/connectors"],
          ];
          const first = order.find(([key]) => pages[key] && pages[key].length);
          location.href = first ? first[1] : "/";
        } catch {
          location.href = "/";
        }
      } catch (e) {
        show(error, e.message, true);
      }
    });
  }

  function publicDebugLink(link) {
    if (!link) return "";
    try {
      const parsed = new URL(link, window.location.origin);
      return `${window.location.origin}${parsed.pathname}${parsed.search}`;
    } catch {
      return link;
    }
  }

  function bindRegister() {
    const form = document.getElementById("register-form");
    const error = document.getElementById("error");
    const message = document.getElementById("message");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      show(error, "", true);
      show(message, "", false);
      try {
        const result = await api("/api/auth/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: document.getElementById("email").value.trim() }),
        });
        let text = result.message;
        const link = publicDebugLink(result.debug_link);
        if (link) text += ` Open this link to continue: ${link}`;
        show(message, text, false);
      } catch (e) {
        show(error, e.message, true);
      }
    });
  }

  function bindForgot() {
    const form = document.getElementById("forgot-form");
    const error = document.getElementById("error");
    const message = document.getElementById("message");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      show(error, "", true);
      show(message, "", false);
      try {
        const result = await api("/api/auth/forgot-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: document.getElementById("email").value.trim() }),
        });
        let text = result.message;
        const link = publicDebugLink(result.debug_link);
        if (link) text += ` Open this link to continue: ${link}`;
        show(message, text, false);
      } catch (e) {
        show(error, e.message, true);
      }
    });
  }

  async function bindSetPassword() {
    const params = new URLSearchParams(location.search);
    const token = params.get("token") || "";
    const purpose = params.get("purpose") || "verify";
    const error = document.getElementById("error");
    const message = document.getElementById("message");
    const title = document.getElementById("title");
    const subtitle = document.getElementById("subtitle");
    if (purpose === "reset") {
      title.textContent = "Reset password";
      subtitle.textContent = "Choose a new password for your account.";
    }
    try {
      const info = await api(`/api/auth/token?${new URLSearchParams({ token, purpose })}`);
      document.getElementById("email").value = info.email;
    } catch (e) {
      show(error, e.message, true);
      document.getElementById("set-form").querySelector("button").disabled = true;
      return;
    }
    document.getElementById("set-form").addEventListener("submit", async event => {
      event.preventDefault();
      show(error, "", true);
      show(message, "", false);
      try {
        const result = await api("/api/auth/set-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token,
            purpose,
            password: document.getElementById("password").value,
            confirm: document.getElementById("confirm").value,
          }),
        });
        show(message, result.message + " Redirecting to sign in…", false);
        setTimeout(() => { location.href = "/login"; }, 1200);
      } catch (e) {
        show(error, e.message, true);
      }
    });
  }

  return { bindLogin, bindRegister, bindForgot, bindSetPassword, bindPasswordToggle };
})();

const AuthPerms = (() => {
  const CACHE_KEY = "acp_auth_status_v1";
  let status = null;
  try {
    const cached = sessionStorage.getItem(CACHE_KEY);
    if (cached) status = JSON.parse(cached);
  } catch (_) {
    status = null;
  }

  const NAV_ITEMS = [
    { href: "/", page: "dashboard", label: "ACP Importer" },
    { href: "/clone", page: "clone", label: "ACP Clone" },
    { href: "/calendar", page: "calendar", label: "Delivery Calendar" },
    { href: "/releases", page: "releases", label: "Jira Releases" },
    { href: "/connectors", page: "connectors", label: "Connectors" },
  ];

  const ADMIN_ITEMS = [
    { href: "/admin/permission-sets", page: "administration", label: "Permission Sets" },
    { href: "/admin/users", page: "administration", label: "User Permissions" },
  ];

  async function load() {
    const response = await fetch("/api/auth/status");
    status = await response.json();
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify(status));
    } catch (_) {}
    return status;
  }

  function clearCache() {
    status = null;
    try {
      sessionStorage.removeItem(CACHE_KEY);
    } catch (_) {}
  }

  function getStatus() {
    return status;
  }

  function hasPage(pageKey) {
    if (!status || !status.enabled) return true;
    if (!status.authenticated) return false;
    if (status.isSuperAdmin) return true;
    const pages = (status.permissions && status.permissions.pages) || {};
    return Boolean(pages[pageKey] && pages[pageKey].length);
  }

  function hasFunction(pageKey, functionKey) {
    if (!status || !status.enabled) return true;
    if (!status.authenticated) return false;
    if (status.isSuperAdmin) return true;
    const pages = (status.permissions && status.permissions.pages) || {};
    return (pages[pageKey] || []).includes(functionKey);
  }

  function navLinksHtml(activeHref) {
    const tools = NAV_ITEMS.filter(item => hasPage(item.page))
      .map(item => {
        const active = item.href === activeHref || (item.href !== "/" && activeHref.startsWith(item.href));
        return `<a class="${active ? "active" : ""}" href="${item.href}">${item.label}</a>`;
      })
      .join("");
    let admin = "";
    if (hasPage("administration")) {
      admin = ADMIN_ITEMS.map(item => {
        const active = activeHref === item.href || activeHref.startsWith(item.href);
        return `<a class="${active ? "active" : ""}" href="${item.href}">${item.label}</a>`;
      }).join("");
    }
    return { tools, admin };
  }

  function sidebarShell(activeHref, toolsHtml, adminHtml) {
    const adminBlock = adminHtml
      ? `<div id="sidebar-admin" class="sidebar-admin-block"><div class="sidebar-section">Administration</div><nav class="sidebar-nav sidebar-nav-admin">${adminHtml}</nav></div>`
      : "";
    return `<aside class="sidebar">
      <div class="sidebar-brand"><span class="sidebar-mark" aria-hidden="true"></span><span>ACP IMPORTER</span></div>
      <div class="sidebar-section">Tools</div>
      <nav class="sidebar-nav sidebar-nav-tools">${toolsHtml || ""}</nav>
      ${adminBlock}
      <div class="sidebar-footer"><div class="sidebar-user" id="sidebar-user"></div><button type="button" id="sign-out" class="secondary sidebar-signout">Sign out</button></div>
    </aside>`;
  }

  function sidebarHtml(activeHref) {
    if (!status) {
      const current = NAV_ITEMS.find(item => item.href === activeHref || (item.href !== "/" && activeHref.startsWith(item.href)));
      const tools = current ? `<a class="active" href="${current.href}">${current.label}</a>` : "";
      return sidebarShell(activeHref, tools, "");
    }
    const { tools, admin } = navLinksHtml(activeHref);
    return sidebarShell(activeHref, tools, admin);
  }

  function applyToExistingSidebar(activeHref) {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;
    const { tools, admin } = status
      ? navLinksHtml(activeHref || location.pathname)
      : (() => {
          const href = activeHref || location.pathname;
          const current = NAV_ITEMS.find(item => item.href === href || (item.href !== "/" && href.startsWith(item.href)));
          return { tools: current ? `<a class="active" href="${current.href}">${current.label}</a>` : "", admin: "" };
        })();

    // Drop any duplicate admin blocks left from earlier renders.
    sidebar.querySelectorAll("#sidebar-admin, .sidebar-admin-block").forEach(el => el.remove());
    sidebar.querySelectorAll(".sidebar-nav").forEach((navEl, index) => {
      if (index > 0) navEl.remove();
    });
    sidebar.querySelectorAll(".sidebar-section").forEach(section => {
      if (/administration/i.test(section.textContent || "")) section.remove();
    });

    let toolNav = sidebar.querySelector(".sidebar-nav-tools") || sidebar.querySelector(".sidebar-nav");
    if (toolNav) {
      toolNav.classList.add("sidebar-nav-tools");
      toolNav.innerHTML = tools;
    }

    const footer = sidebar.querySelector(".sidebar-footer");
    if (admin) {
      const adminBlock = document.createElement("div");
      adminBlock.id = "sidebar-admin";
      adminBlock.className = "sidebar-admin-block";
      adminBlock.innerHTML = `<div class="sidebar-section">Administration</div><nav class="sidebar-nav sidebar-nav-admin">${admin}</nav>`;
      if (footer) sidebar.insertBefore(adminBlock, footer);
      else sidebar.appendChild(adminBlock);
    }
  }

  function paintCachedSidebar() {
    if (!document.querySelector(".sidebar")) return;
    applyToExistingSidebar(location.pathname);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", paintCachedSidebar);
  } else {
    paintCachedSidebar();
  }

  return { load, clearCache, getStatus, hasPage, hasFunction, sidebarHtml, applyToExistingSidebar, NAV_ITEMS };
})();

async function mountSessionChrome(activeHref, options = {}) {
  const userEl = document.getElementById("sidebar-user");
  const signOut = document.getElementById("sign-out");
  try {
    const data = await AuthPerms.load();
    if (!options.skipNav) {
      AuthPerms.applyToExistingSidebar(activeHref || location.pathname);
    }
    const liveUser = document.getElementById("sidebar-user");
    const liveSignOut = document.getElementById("sign-out");
    if (liveUser) {
      liveUser.textContent = data.email || "";
      if (data.isSuperAdmin) liveUser.textContent += " (admin)";
    }
    if (liveSignOut) {
      liveSignOut.hidden = !data.authenticated;
      liveSignOut.onclick = async () => {
        if (typeof AuthPerms !== "undefined" && AuthPerms.clearCache) AuthPerms.clearCache();
        await fetch("/api/auth/logout", { method: "POST" });
        location.href = "/login";
      };
    }
  } catch (_) {}
  void userEl;
  void signOut;
}

document.addEventListener("DOMContentLoaded", () => mountSessionChrome());

