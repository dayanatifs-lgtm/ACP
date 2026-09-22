const h = React.createElement;

async function loadJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Could not load Jira releases.");
  return payload;
}

function Releases() {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState("");
  const load = async () => { setError(""); try { setData(await loadJson("/api/releases")); } catch (e) { setError(e.message); } };
  React.useEffect(() => { load(); }, []);
  React.useEffect(() => { if (typeof mountSessionChrome === "function") mountSessionChrome(); }, []);
  return h("div", { className: "app-frame" },
    h("aside", { className: "sidebar" },
      h("div", { className: "sidebar-brand" }, h("span", { className: "sidebar-mark", "aria-hidden": "true" }), h("span", null, "ACP IMPORTER")),
      h("div", { className: "sidebar-section" }, "Tools"),
      h("nav", { className: "sidebar-nav" },
        h("a", { className: "", href: "/" }, "ACP Importer"),
        h("a", { className: "", href: "/clone" }, "ACP Deploy"),
        h("a", { className: "", href: "/calendar" }, "Delivery Calendar"),
        h("a", { className: "active", href: "/releases" }, "Jira Releases"),
        h("a", { className: "", href: "/connectors" }, "Connectors")
      ),
      h("div", { className: "sidebar-footer" },
        h("div", { className: "sidebar-user", id: "sidebar-user" }),
        h("button", { type: "button", id: "sign-out", className: "secondary sidebar-signout" }, "Sign out")
      )
    ),
    h("div", { className: "main-panel" },
      h("div", { className: "shell" },
        h("header", null, h("p", { className: "eyebrow" }, "JIRA"), h("h1", null, "Releases"), h("p", { className: "subtitle" }, data ? `Project ${data.project}` : "Loading release information…")),
        h("section", { className: "card" },
          h("div", { className: "section-heading" }, h("h2", null, "Available releases"), h("button", { className: "secondary", onClick: load }, "Refresh")),
          error && h("p", { className: "error" }, error),
          data && h("div", { className: "release-list" }, data.releases.map(release => h("article", { className: "release", key: release.id },
            h("div", { className: "release-title" }, h("a", { className: "release-name", href: `/releases/${encodeURIComponent(release.id)}` }, release.name), h("span", { className: release.released ? "release-state released" : "release-state unreleased" }, release.released ? "RELEASED" : "UNRELEASED"), release.archived && h("span", { className: "release-state archived" }, "ARCHIVED")),
            h("dl", null, h("div", null, h("dt", null, "Release ID"), h("dd", null, release.id)), h("div", null, h("dt", null, "Release date"), h("dd", null, release.releaseDate))),
            h("p", { className: "release-description" }, release.description),
            h("a", { className: "show-items", href: `/releases/${encodeURIComponent(release.id)}` }, "View included items")
          )))
        )
      )
    )
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(h(Releases));
