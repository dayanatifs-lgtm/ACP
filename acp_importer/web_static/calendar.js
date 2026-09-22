const h = React.createElement;
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const ENVIRONMENTS = ["STG", "UAT", "CFG", "PROD"];
const TYPES = ["Build", "Delivery", "Manual Delivery"];

function iso(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseIso(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function startOfWeek(date) {
  const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  copy.setDate(copy.getDate() - copy.getDay());
  return copy;
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const start = startOfWeek(first);
  return Array.from({ length: 42 }, (_, index) => addDays(start, index));
}

function eventClass(item) {
  if (item.environment === "CFG") return "cal-event cfg";
  if (String(item.deliveryType).toLowerCase().includes("build")) return "cal-event build";
  return "cal-event delivery";
}

function emptyForm(date) {
  return { name: "UAT Delivery", date: date || iso(new Date()), environment: "UAT", deliveryType: "Delivery", description: "", itemsText: "" };
}

async function loadJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Request failed.");
  return payload;
}

function Nav() {
  React.useEffect(() => { if (typeof mountSessionChrome === "function") mountSessionChrome(); }, []);
  return h("aside", { className: "sidebar" },
    h("div", { className: "sidebar-brand" }, h("span", { className: "sidebar-mark", "aria-hidden": "true" }), h("span", null, "ACP IMPORTER")),
    h("div", { className: "sidebar-section" }, "Tools"),
    h("nav", { className: "sidebar-nav" },
      h("a", { className: "", href: "/" }, "ACP Importer"),
      h("a", { className: "", href: "/clone" }, "ACP Deploy"),
      h("a", { className: "active", href: "/calendar" }, "Delivery Calendar"),
      h("a", { className: "", href: "/releases" }, "Jira Releases"),
      h("a", { className: "", href: "/connectors" }, "Connectors")
    ),
    h("div", { className: "sidebar-footer" },
      h("div", { className: "sidebar-user", id: "sidebar-user" }),
      h("button", { type: "button", id: "sign-out", className: "secondary sidebar-signout" }, "Sign out")
    )
  );
}

function Modal({ title, children, onClose }) {
  return h("div", { className: "modal-backdrop", onClick: event => event.target === event.currentTarget && onClose() },
    h("div", { className: "modal", role: "dialog", "aria-modal": "true" },
      h("div", { className: "modal-heading" }, h("h2", null, title), h("button", { className: "secondary", onClick: onClose }, "Close")),
      children)
  );
}

function DeliveryForm({ initial, onCancel, onSaved }) {
  const [form, setForm] = React.useState(initial);
  const [preview, setPreview] = React.useState(null);
  const [error, setError] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const nameTouched = React.useRef(Boolean(initial.id));

  React.useEffect(() => {
    let cancelled = false;
    setPreview(null);
    loadJson(`/api/deliveries/preview-release?date=${encodeURIComponent(form.date)}`)
      .then(data => { if (!cancelled) setPreview(data); })
      .catch(err => { if (!cancelled) setPreview({ jiraRelease: null, jiraMatchNote: err.message }); });
    return () => { cancelled = true; };
  }, [form.date]);

  function setField(field, value) {
    setForm(current => {
      const next = { ...current, [field]: value };
      if (!nameTouched.current && (field === "environment" || field === "deliveryType")) {
        next.name = `${field === "environment" ? value : next.environment} ${field === "deliveryType" ? value : next.deliveryType}`;
      }
      return next;
    });
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const body = {
      name: form.name,
      date: form.date,
      environment: form.environment,
      deliveryType: form.deliveryType,
      description: form.description,
      items: form.itemsText.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
    };
    try {
      const saved = await loadJson(form.id ? `/api/deliveries/${encodeURIComponent(form.id)}` : "/api/deliveries", {
        method: form.id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      onSaved(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return h("form", { className: "delivery-form", onSubmit: save },
    h("label", null, "Delivery name", h("input", { required: true, value: form.name, onChange: event => { nameTouched.current = true; setField("name", event.target.value); } })),
    h("label", null, "Delivery date", h("input", { type: "date", required: true, value: form.date, onChange: event => setField("date", event.target.value) })),
    h("label", null, "Environment", h("select", { value: form.environment, onChange: event => setField("environment", event.target.value) }, ENVIRONMENTS.map(value => h("option", { key: value, value }, value)))),
    h("label", null, "Delivery type", h("select", { value: form.deliveryType, onChange: event => setField("deliveryType", event.target.value) }, TYPES.map(value => h("option", { key: value, value }, value)))),
    h("label", { className: "full" }, "Delivery description", h("textarea", { rows: 3, value: form.description, onChange: event => setField("description", event.target.value) })),
    h("label", { className: "full" }, "Delivery items (one per line)", h("textarea", { rows: 4, value: form.itemsText, onChange: event => setField("itemsText", event.target.value), placeholder: "ACP package or change item" })),
    preview && h("p", { className: "help full" }, preview.jiraRelease
      ? `Applicable Jira release: ${preview.jiraRelease.name} (${preview.jiraRelease.releaseDate}).`
      : preview.jiraMatchNote),
    error && h("p", { className: "error full" }, error),
    h("div", { className: "buttons full" },
      h("button", { type: "button", className: "secondary", onClick: onCancel }, "Cancel"),
      h("button", { type: "submit", className: "primary", disabled: saving }, saving ? "Saving…" : "Save delivery")
    )
  );
}

function Details({ item, onClose, onEdit, onDeleted }) {
  const [error, setError] = React.useState("");
  async function remove() {
    if (!window.confirm(`Remove ${item.name}?`)) return;
    try {
      await loadJson(`/api/deliveries/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      onDeleted();
    } catch (err) {
      setError(err.message);
    }
  }
  const jira = item.jiraRelease;
  return h(Modal, { title: item.name, onClose },
    h("dl", { className: "detail-grid" },
      h("div", null, h("dt", null, "Delivery date"), h("dd", null, item.date)),
      h("div", null, h("dt", null, "Environment"), h("dd", null, item.environment)),
      h("div", null, h("dt", null, "Delivery type"), h("dd", null, item.deliveryType)),
      h("div", null, h("dt", null, "Release version"), h("dd", null, item.releaseVersion || "Not linked")),
      h("div", { className: "full" }, h("dt", null, "Jira release"), h("dd", null, jira
        ? h("a", { className: "release-link", href: `/releases/${encodeURIComponent(jira.id)}` }, jira.name)
        : "No matching Jira release")),
      h("div", { className: "full" }, h("dt", null, "Description"), h("dd", null, item.description || "—")),
      h("div", { className: "full" }, h("dt", null, "Jira match"), h("dd", null, item.jiraMatchNote || "—"))
    ),
    h("h3", null, "Delivery items"),
    item.items && item.items.length
      ? h("ul", { className: "item-bullets" }, item.items.map((entry, index) => h("li", { key: index }, entry)))
      : h("p", { className: "help" }, "No delivery items were recorded."),
    jira && h("p", { className: "help" }, "Open the Jira release to view included items and pull requests."),
    error && h("p", { className: "error" }, error),
    h("div", { className: "buttons" },
      h("button", { className: "secondary", onClick: onEdit }, "Edit"),
      h("button", { className: "danger", onClick: remove }, "Delete"),
      h("button", { className: "primary", onClick: onClose }, "Close")
    )
  );
}

function Calendar() {
  const today = iso(new Date());
  const [cursor, setCursor] = React.useState(() => new Date());
  const [view, setView] = React.useState("month");
  const [deliveries, setDeliveries] = React.useState([]);
  const [error, setError] = React.useState("");
  const [form, setForm] = React.useState(null);
  const [selected, setSelected] = React.useState(null);

  const load = React.useCallback(async () => {
    setError("");
    try {
      const data = await loadJson("/api/deliveries");
      setDeliveries(data.deliveries);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    const onKey = event => { if (event.key === "Escape") { setForm(null); setSelected(null); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const byDate = React.useMemo(() => {
    const grouped = {};
    for (const item of deliveries) {
      grouped[item.date] = grouped[item.date] || [];
      grouped[item.date].push(item);
    }
    return grouped;
  }, [deliveries]);

  const days = view === "month"
    ? monthGrid(cursor.getFullYear(), cursor.getMonth())
    : Array.from({ length: 7 }, (_, index) => addDays(startOfWeek(cursor), index));

  function shift(amount) {
    setCursor(current => view === "month"
      ? new Date(current.getFullYear(), current.getMonth() + amount, 1)
      : addDays(current, amount * 7));
  }

  function openNew(date) {
    setSelected(null);
    setForm(emptyForm(date));
  }

  function heading() {
    if (view === "month") return `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`;
    const start = startOfWeek(cursor);
    const end = addDays(start, 6);
    return `${MONTHS[start.getMonth()]} ${start.getDate()} – ${MONTHS[end.getMonth()]} ${end.getDate()}, ${end.getFullYear()}`;
  }

  return h("div", { className: "app-frame" },
    h(Nav),
    h("div", { className: "main-panel" },
      h("div", { className: "shell calendar-shell" },
    h("header", null,
      h("p", { className: "eyebrow" }, "DELIVERY PLANNING"),
      h("h1", null, "Delivery Calendar"),
      h("p", { className: "subtitle" }, "Add deliveries to a date. The latest applicable Jira release is linked automatically when one exists.")
    ),
    h("section", { className: "card calendar-card" },
      h("div", { className: "cal-toolbar" },
        h("div", { className: "cal-nav" },
          h("button", { className: "secondary", onClick: () => setCursor(new Date()) }, "Today"),
          h("button", { className: "icon-btn", onClick: () => shift(-1), "aria-label": "Previous" }, "‹"),
          h("button", { className: "icon-btn", onClick: () => shift(1), "aria-label": "Next" }, "›"),
          h("h2", null, heading())
        ),
        h("div", { className: "buttons" },
          h("button", { className: view === "month" ? "primary" : "secondary", onClick: () => setView("month") }, "Month"),
          h("button", { className: view === "week" ? "primary" : "secondary", onClick: () => setView("week") }, "Week"),
          h("button", { className: "primary", onClick: () => openNew(today) }, "+ New")
        )
      ),
      error && h("p", { className: "error" }, error),
      h("div", { className: "cal-weekdays" }, WEEKDAYS.map(day => h("div", { key: day }, day))),
      h("div", { className: view === "week" ? "cal-grid week" : "cal-grid" }, days.map(day => {
        const key = iso(day);
        const outside = view === "month" && day.getMonth() !== cursor.getMonth();
        return h("div", {
          key,
          className: `cal-cell${outside ? " outside" : ""}${key === today ? " today" : ""}`,
          onClick: event => { if (event.target === event.currentTarget || event.target.classList.contains("cal-date")) openNew(key); }
        },
          h("span", { className: "cal-date" }, day.getDate()),
          (byDate[key] || []).map(item => h("button", {
            key: item.id,
            className: eventClass(item),
            title: item.name,
            onClick: event => { event.stopPropagation(); setForm(null); setSelected(item); }
          }, item.name))
        );
      }))
    ),
    form && h(Modal, { title: form.id ? "Edit delivery" : "Add delivery", onClose: () => setForm(null) },
      h(DeliveryForm, {
        initial: form,
        onCancel: () => setForm(null),
        onSaved: saved => { setForm(null); setSelected(saved); load(); }
      })
    ),
    selected && !form && h(Details, {
      item: selected,
      onClose: () => setSelected(null),
      onEdit: () => setForm({ ...selected, itemsText: (selected.items || []).join("\n") }),
      onDeleted: () => { setSelected(null); load(); }
    })
      )
    )
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(Calendar));
