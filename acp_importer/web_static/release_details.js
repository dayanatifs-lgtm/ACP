const h = React.createElement;

async function loadDetails() {
  const id = location.pathname.split("/").filter(Boolean).pop();
  const response = await fetch(`/api/releases/${encodeURIComponent(id)}/details`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Could not load release details.");
  return payload;
}

function PullRequests({ tasks }) {
  const links = tasks.flatMap(task => task.pullRequests.map(pr => ({ ...pr, task })));
  if (!links.length) return h("span", {className:"not-available"}, "Not available");
  return h("div", {className:"pr-links"}, links.map((pr, index) => h("a", {key:`${pr.task.key}-${index}`, className:"pr-link", href:pr.url, target:"_blank", rel:"noopener noreferrer"}, pr.label || "Open PR")));
}

function ReleaseDetails() {
  const [data, setData] = React.useState(null); const [error, setError] = React.useState("");
  React.useEffect(() => { loadDetails().then(setData).catch(e => setError(e.message)); }, []);
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
        h("header", null, h("p", { className: "eyebrow" }, "JIRA RELEASE DETAILS"), h("h1", null, data ? data.release.name : "Release details"), h("p", { className: "subtitle" }, data ? `Project ${data.project} · Release ID ${data.release.id}` : "Loading release items and pull requests…")),
        error && h("section", { className: "card" }, h("p", { className: "error" }, error)),
        data && h("section", { className: "card" },
          h("div", { className: "section-heading" }, h("h2", null, "Release items"), h("a", { className: "secondary button-link", href: "/releases" }, "Back to releases")),
          h("p", { className: "subtitle" }, `${data.items.length} item${data.items.length === 1 ? "" : "s"}. Pull requests are read from Development Task web links.`),
          h("div", { className: "items-table-wrap" }, h("table", { className: "items-table" },
            h("thead", null, h("tr", null, h("th", null, "Jira item"), h("th", null, "Summary"), h("th", null, "Status"), h("th", null, "Development Task"), h("th", null, "Pull Request"))),
            h("tbody", null, data.items.map(item => h("tr", { key: item.id },
              h("td", null, h("a", { href: item.browseUrl, target: "_blank", rel: "noopener noreferrer" }, item.key)),
              h("td", null, item.summary), h("td", null, item.status),
              h("td", null, item.developmentTasks.map((task, index) => h("div", { key: task.key, className: "task-link" }, h("a", { href: task.browseUrl, target: "_blank", rel: "noopener noreferrer" }, task.key), index === 0 && task.summary !== "—" && h("small", null, task.summary)))),
              h("td", null, h(PullRequests, { tasks: item.developmentTasks }))
            )))
          ))
        )
      )
    )
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(h(ReleaseDetails));
