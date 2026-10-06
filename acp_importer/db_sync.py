"""CFG to DEV Oracle schema compare and data sync.

Not OS Data Pump (expdp/impdp). Uses python-oracledb to:
1) compare tables/columns
2) optionally ADD missing nullable columns on target
3) copy rows in FK-aware order where possible

Passwords are never written to disk by this module.

Many IFS Oracle listeners use Native Network Encryption. Thin mode then fails
with DPY-4011/DPY-6005; enable thick mode (Oracle Instant Client) for those DBs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger("DbSync")

IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
ROW_LIMIT = 1000

_MODIFY_NAMES = (
    "LAST_UPDATED",
    "LAST_UPDATE",
    "LAST_UPDATED_DATE",
    "LAST_UPDATE_DATE",
    "UPDATED_AT",
    "UPDATED_DATE",
    "UPDATE_DATE",
    "MODIFIED_AT",
    "MODIFIED_DATE",
    "MODIFY_DATE",
    "CHANGED_DATE",
    "CHANGE_DATE",
    "DT_CHG",
    "LAST_CHANGED",
    "ROWVERSION",
    "OBJVERSION",
)
_CREATE_NAMES = (
    "CREATED_AT",
    "CREATED_DATE",
    "CREATE_DATE",
    "CREATION_DATE",
    "DT_CRE",
    "INSERTED_AT",
    "INSERT_DATE",
    "REG_DATE",
    "ENTERED_DATE",
    "CREATED",
)
_ORDER_NAME_HINTS = ("UPDATE", "UPDATED", "MODIF", "CHANGE", "CHANGED", "CREATE", "CREATED", "ROWVERSION", "OBJVERSION")
_UNRELIABLE_KEY_NAMES = {"ROWKEY", "OBJKEY", "GUID", "UUID", "ROWID"}

_thick_lock = threading.Lock()
_thick_state: dict[str, Any] = {
    "attempted": False,
    "enabled": False,
    "lib_dir": None,
    "config_dir": None,
    "error": None,
}


def _bundled_net_dir() -> Path:
    return Path(__file__).resolve().parent / "oracle_net"


def _dir_has_sqlnet(path: Path) -> bool:
    return path.is_dir() and (path / "sqlnet.ora").is_file()


def _net_config_dir() -> str:
    """Return a TNS_ADMIN that actually exists on this machine.

    Ignore stale TNS_ADMIN values copied from a laptop (they cause ORA-12569
    because sqlnet.ora never loads and a 19c dbhome network/admin is used).
    """
    bundled = _bundled_net_dir()
    for key in ("ORACLE_NET_CONFIG_DIR", "TNS_ADMIN"):
        value = (os.environ.get(key) or "").strip().strip('"')
        if value:
            candidate = Path(value)
            if _dir_has_sqlnet(candidate):
                return str(candidate)
            LOG.warning("Ignoring %s=%s (sqlnet.ora not found)", key, value)
    return str(bundled)


@dataclass
class OracleEndpoint:
    host: str
    port: int = 1521
    service: str = ""
    user: str = ""
    password: str = ""
    connect_as: str = "service"  # "service" | "sid"
    thick: bool = False

    def connect_mode(self) -> str:
        mode = (self.connect_as or "service").strip().lower()
        return "sid" if mode == "sid" else "service"

    def dsn(self, oracledb_mod: Any | None = None) -> str:
        host = (self.host or "").strip()
        service = (self.service or "").strip()
        if not host or not service:
            raise ValueError("Host and service/SID are required")
        port = int(self.port or 1521)
        if oracledb_mod is None:
            oracledb_mod = _require_oracledb()
        if self.connect_mode() == "sid":
            return oracledb_mod.makedsn(host, port, sid=service)
        return oracledb_mod.makedsn(host, port, service_name=service)


@dataclass
class SyncJob:
    running: bool = False
    cancel_requested: bool = False
    phase: str = "idle"
    message: str = "Ready"
    current_table: str = ""
    current_action: str = ""
    completed: int = 0
    total: int = 0
    rows_copied: int = 0
    selected_tables: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    compare: dict[str, Any] | None = None


_jobs: dict[str, SyncJob] = {}
_lock = threading.RLock()
_STATE_DIR = Path(__file__).resolve().parents[1] / ".acp_db_sync"


def _state_path(owner: str | None) -> Path:
    safe = re.sub(r"[^a-z0-9_.@-]+", "_", (owner or "anonymous").strip().lower()) or "anonymous"
    return _STATE_DIR / f"{safe}.json"


def _save_selection(owner: str | None, schema: str, tables: list[str]) -> None:
    path = _state_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": schema, "tables": tables}, indent=2),
        encoding="utf-8",
    )


def _load_selection(owner: str | None) -> list[str]:
    path = _state_path(owner)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(name) for name in (data.get("tables") or [])]


def _job(owner: str | None) -> SyncJob:
    key = (owner or "anonymous").strip().lower() or "anonymous"
    with _lock:
        if key not in _jobs:
            _jobs[key] = SyncJob()
        return _jobs[key]


def get_status(owner: str | None) -> dict[str, Any]:
    job = _job(owner)
    with _lock:
        status = asdict(job)
        results = list(job.results)
        compare = job.compare
    status["selectedCount"] = len(status.pop("selected_tables", []) or []) or len(_load_selection(owner))
    status["resultsTotal"] = len(results)
    status["succeededCount"] = sum(1 for row in results if row.get("success") and not row.get("skipped"))
    status["failedCount"] = sum(1 for row in results if not row.get("success"))
    status["skippedCount"] = sum(1 for row in results if row.get("skipped"))
    status["results"] = results[-30:]
    if compare and len(compare.get("tables") or []) > 80:
        issues = [t for t in compare["tables"] if t.get("status") not in {"ok", None}]
        status["compare"] = {
            "schema": compare.get("schema"),
            "tableCount": len(compare["tables"]),
            "issueCount": len(issues),
            "tables": issues[:80],
        }
    status["thick"] = {
        "enabled": bool(_thick_state["enabled"]),
        "libDir": _thick_state["lib_dir"],
        "configDir": _thick_state["config_dir"],
        "error": _thick_state["error"],
    }
    return status


def request_stop(owner: str | None) -> None:
    job = _job(owner)
    with _lock:
        if not job.running:
            raise ValueError("No DB sync is currently running.")
        job.cancel_requested = True
        where = f" while on {job.current_table}" if job.current_table else ""
        action = f" ({job.current_action})" if job.current_action else ""
        job.message = f"Stop requested{where}{action}"


def _require_oracledb():
    try:
        import oracledb
    except ImportError as exc:
        raise ValueError(
            "Python package 'oracledb' is not installed. Run: pip install oracledb"
        ) from exc
    return oracledb


def _is_dbhome_client(path: str) -> bool:
    lower = path.lower().replace("/", "\\")
    return "dbhome" in lower or ("\\product\\" in lower and "instantclient" not in lower)


def _is_instant_client(path: str) -> bool:
    return "instantclient" in path.lower()


def _candidate_client_dirs() -> list[str]:
    dirs: list[str] = []
    for key in ("ORACLE_CLIENT_LIB_DIR", "ORACLE_HOME"):
        value = (os.environ.get(key) or "").strip().strip('"')
        if value:
            dirs.append(value)
            bin_dir = str(Path(value) / "bin")
            if bin_dir not in dirs:
                dirs.append(bin_dir)

    for base in (Path(r"C:\oracle"), Path(r"C:\Oracle"), Path(r"D:\oracle"), Path(r"C:\instantclient"), Path(r"C:\app")):
        if not base.exists():
            continue
        try:
            for match in base.rglob("oci.dll"):
                parent = str(match.parent)
                if _is_instant_client(parent) or "client" in parent.lower():
                    dirs.append(parent)
                elif not _is_dbhome_client(parent):
                    dirs.append(parent)
        except Exception:
            LOG.debug("Could not scan %s for Instant Client", base, exc_info=True)

    for part in (os.environ.get("PATH") or "").split(os.pathsep):
        part = part.strip().strip('"')
        if not part:
            continue
        if (Path(part) / "oci.dll").exists() or (Path(part) / "libclntsh.so").exists():
            dirs.append(part)

    seen: set[str] = set()
    unique: list[str] = []
    for item in dirs:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    preferred = [p for p in unique if _is_instant_client(p)]
    other = [p for p in unique if p not in preferred and not _is_dbhome_client(p)]
    dbhomes = [p for p in unique if _is_dbhome_client(p)]
    return preferred + other + dbhomes


def ensure_thick_mode(lib_dir: str | None = None) -> dict[str, Any]:
    """Initialize Oracle Instant Client (thick mode) once per process."""
    oracledb = _require_oracledb()
    with _thick_lock:
        if _thick_state["enabled"]:
            return {"ok": True, "libDir": _thick_state["lib_dir"], "already": True}
        candidates: list[str] = []
        if lib_dir and lib_dir.strip():
            candidates.append(lib_dir.strip())
        candidates.extend(_candidate_client_dirs())
        candidates.append("")  # empty = PATH / default search

        last_error: Exception | None = None
        config_dir = _net_config_dir()
        os.environ["TNS_ADMIN"] = config_dir
        for candidate in candidates:
            try:
                kwargs: dict[str, Any] = {}
                if candidate:
                    kwargs["lib_dir"] = candidate
                if config_dir:
                    kwargs["config_dir"] = config_dir
                oracledb.init_oracle_client(**kwargs)
                _thick_state["attempted"] = True
                _thick_state["enabled"] = True
                _thick_state["lib_dir"] = candidate or "(PATH/default)"
                _thick_state["config_dir"] = config_dir
                _thick_state["error"] = None
                LOG.info("Oracle thick mode enabled via %s (TNS %s)", _thick_state["lib_dir"], config_dir)
                return {"ok": True, "libDir": _thick_state["lib_dir"], "configDir": config_dir}
            except Exception as exc:
                msg = str(exc).lower()
                if "already been initialized" in msg or "dpi-1012" in msg:
                    _thick_state["attempted"] = True
                    _thick_state["enabled"] = True
                    _thick_state["lib_dir"] = candidate or _thick_state["lib_dir"] or "(PATH/default)"
                    _thick_state["config_dir"] = _thick_state["config_dir"] or config_dir
                    _thick_state["error"] = None
                    return {
                        "ok": True,
                        "libDir": _thick_state["lib_dir"],
                        "configDir": _thick_state["config_dir"],
                        "already": True,
                    }
                last_error = exc
                continue

        _thick_state["attempted"] = True
        _thick_state["enabled"] = False
        _thick_state["error"] = str(last_error) if last_error else "Instant Client not found"
        raise ValueError(
            "Thick mode failed: Oracle Instant Client not found. "
            "Install Instant Client on the app server and set ORACLE_CLIENT_LIB_DIR "
            "to the folder that contains oci.dll, then restart the app. "
            f"Detail: {_thick_state['error']}"
        )


def thick_status() -> dict[str, Any]:
    return {
        "enabled": bool(_thick_state["enabled"]),
        "libDir": _thick_state["lib_dir"],
        "configDir": _thick_state["config_dir"] or _net_config_dir(),
        "error": _thick_state["error"],
        "candidates": _candidate_client_dirs()[:8],
    }


def _quote_ident(name: str) -> str:
    text = (name or "").strip()
    if not text or not IDENTIFIER_RE.match(text):
        raise ValueError(f"Invalid Oracle identifier: {name!r}")
    return text.upper()


def _connect(endpoint: OracleEndpoint):
    oracledb = _require_oracledb()
    if endpoint.thick:
        ensure_thick_mode()
    if not (endpoint.user or "").strip() or endpoint.password is None:
        raise ValueError("Username and password are required")
    kwargs: dict[str, Any] = {
        "user": endpoint.user.strip(),
        "password": endpoint.password,
        "host": (endpoint.host or "").strip(),
        "port": int(endpoint.port or 1521),
        "disable_oob": True,
    }
    name = (endpoint.service or "").strip()
    if not kwargs["host"] or not name:
        raise ValueError("Host and service/SID are required")
    if endpoint.connect_mode() == "sid":
        kwargs["sid"] = name
    else:
        kwargs["service_name"] = name
    try:
        return oracledb.connect(**kwargs)
    except Exception as first:
        if not _should_retry_connect_mode(first):
            raise
        alt = dict(kwargs)
        alt.pop("sid", None)
        alt.pop("service_name", None)
        if "sid" in kwargs:
            alt["service_name"] = name
        else:
            alt["sid"] = name
        LOG.info("Retrying Oracle connect using %s instead of %s", "service name" if "service_name" in alt else "SID", endpoint.connect_mode())
        try:
            return oracledb.connect(**alt)
        except Exception:
            raise first from None


def _should_retry_connect_mode(exc: Exception) -> bool:
    text = str(exc).upper()
    markers = (
        "ORA-12569",
        "ORA-12514",
        "ORA-12505",
        "ORA-12541",
        "DPY-6001",
        "DPY-6005",
        "DPY-4011",
        "TNS:PACKET CHECKSUM",
        "LISTENER DOES NOT CURRENTLY KNOW",
    )
    return any(marker in text for marker in markers)


def _friendly_connect_error(exc: Exception, endpoint: OracleEndpoint) -> str:
    text = str(exc)
    mode = endpoint.connect_mode()
    other = "SID" if mode == "service" else "service name"
    hints = [
        f"Tried connect as {mode} to {endpoint.host}:{endpoint.port or 1521} / {endpoint.service}"
        + (" (thick mode)" if endpoint.thick else " (thin mode)")
        + ".",
        f"If this keeps failing, switch Connect as to {other}.",
        "Confirm the app server can reach the DB host on TCP 1521.",
    ]
    if not endpoint.thick and ("DPY-4011" in text or "DPY-6005" in text or "DPY-3001" in text):
        hints.append(
            "This pattern often means Native Network Encryption — tick "
            "'Use Oracle Instant Client (thick mode)' and install Instant Client on the server."
        )
    if "12569" in text or "packet checksum" in text.lower():
        hints.append(
            "ORA-12569 is an Oracle Net encryption/checksum mismatch. "
            "Restart start_server.bat so TNS_ADMIN is the server copy of acp_importer/oracle_net "
            "(not a laptop path). Prefer Instant Client over a 19c dbhome. "
            "IFS Cloud hosts usually need Connect as = Service name."
        )
    return f"{text} — {' '.join(hints)}"


def test_connection(endpoint: OracleEndpoint) -> dict[str, Any]:
    try:
        with _connect(endpoint) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                select sys_context('USERENV','SESSION_USER') as session_user,
                       sys_context('USERENV','DB_NAME') as db_name,
                       sys_context('USERENV','SERVICE_NAME') as service_name
                from dual
                """
            )
            row = cur.fetchone()
            return {
                "ok": True,
                "sessionUser": row[0],
                "dbName": row[1],
                "serviceName": row[2],
                "dsn": endpoint.dsn(),
                "connectAs": endpoint.connect_mode(),
                "thick": bool(endpoint.thick and _thick_state["enabled"]),
                "thickLibDir": _thick_state["lib_dir"],
            }
    except Exception as exc:
        raise ValueError(_friendly_connect_error(exc, endpoint)) from exc


def list_schemas(endpoint: OracleEndpoint) -> list[str]:
    with _connect(endpoint) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select distinct owner
            from all_tables
            where owner not in (
              'SYS','SYSTEM','XDB','CTXSYS','MDSYS','ORDDATA','ORDSYS','WMSYS',
              'LBACSYS','DVSYS','GSMADMIN_INTERNAL','AUDSYS','OJVMSYS','DBSNMP',
              'OUTLN','APPQOSSYS','REMOTE_SCHEDULER_AGENT'
            )
            order by 1
            """
        )
        return [str(r[0]) for r in cur.fetchall()]


def list_tables(endpoint: OracleEndpoint, schema: str) -> list[str]:
    owner = _quote_ident(schema)
    with _connect(endpoint) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            select table_name
            from all_tables
            where owner = :owner
            order by 1
            """,
            {"owner": owner},
        )
        return [str(r[0]) for r in cur.fetchall()]


def _columns(conn, schema: str, table: str) -> list[dict[str, Any]]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    cur = conn.cursor()
    cur.execute(
        """
        select column_name, data_type, data_length, data_precision, data_scale,
               nullable, data_default, column_id
        from all_tab_columns
        where owner = :owner and table_name = :table_name
        order by column_id
        """,
        {"owner": owner, "table_name": table_name},
    )
    cols = []
    for row in cur.fetchall():
        cols.append(
            {
                "name": row[0],
                "dataType": row[1],
                "dataLength": row[2],
                "dataPrecision": row[3],
                "dataScale": row[4],
                "nullable": row[5] == "Y",
                "dataDefault": row[6],
                "columnId": row[7],
            }
        )
    return cols


def _column_ddl(col: dict[str, Any], *, force_null: bool = True) -> str:
    name = _quote_ident(col["name"])
    dtype = str(col["dataType"] or "VARCHAR2").upper()
    length = col.get("dataLength")
    precision = col.get("dataPrecision")
    scale = col.get("dataScale")
    if dtype in {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW"}:
        size = int(length or 1)
        type_sql = f"{dtype}({size})"
    elif dtype in {"NUMBER", "FLOAT"}:
        if precision is None:
            type_sql = dtype
        elif scale is None:
            type_sql = f"{dtype}({int(precision)})"
        else:
            type_sql = f"{dtype}({int(precision)},{int(scale)})"
    else:
        type_sql = dtype
    return f"{name} {type_sql} {'NULL' if force_null or col.get('nullable', True) else 'NOT NULL'}"


def compare_tables(
    source: OracleEndpoint,
    target: OracleEndpoint,
    schema: str,
    tables: list[str] | None = None,
    on_table=None,
) -> dict[str, Any]:
    owner = _quote_ident(schema)
    with _connect(source) as src, _connect(target) as tgt:
        src_tables = set(list_tables(source, owner))
        tgt_tables = set(list_tables(target, owner))
        selected = [_quote_ident(t) for t in (tables or sorted(src_tables))]
        report = []
        for index, table in enumerate(selected, start=1):
            if on_table and on_table(index, len(selected), table) is False:
                break
            item: dict[str, Any] = {
                "table": table,
                "sourceExists": table in src_tables,
                "targetExists": table in tgt_tables,
            }
            if table not in src_tables:
                item["status"] = "missing_on_source"
                report.append(item)
                continue
            src_cols = {c["name"]: c for c in _columns(src, owner, table)}
            if table not in tgt_tables:
                item["status"] = "missing_on_target"
                item["missingColumns"] = list(src_cols.values())
                item["extraColumns"] = []
                item["typeMismatches"] = []
                report.append(item)
                continue
            tgt_cols = {c["name"]: c for c in _columns(tgt, owner, table)}
            missing = [src_cols[n] for n in src_cols.keys() - tgt_cols.keys()]
            extra = [tgt_cols[n] for n in tgt_cols.keys() - src_cols.keys()]
            mismatches = []
            for name in sorted(src_cols.keys() & tgt_cols.keys()):
                s, t = src_cols[name], tgt_cols[name]
                if (s["dataType"], s["dataLength"], s["dataPrecision"], s["dataScale"]) != (
                    t["dataType"],
                    t["dataLength"],
                    t["dataPrecision"],
                    t["dataScale"],
                ):
                    mismatches.append({"column": name, "source": s, "target": t})
            item["missingColumns"] = missing
            item["extraColumns"] = extra
            item["typeMismatches"] = mismatches
            if missing:
                item["status"] = "columns_missing_on_target"
            elif mismatches:
                item["status"] = "type_mismatch"
            else:
                item["status"] = "ok"
            report.append(item)
        return {"schema": owner, "tables": report}


def _fk_edges(conn, schema: str, tables: set[str]) -> list[tuple[str, str]]:
    owner = _quote_ident(schema)
    cur = conn.cursor()
    cur.execute(
        """
        select a.table_name as child_table, c_pk.table_name as parent_table
        from all_constraints a
        join all_constraints c_pk
          on a.r_owner = c_pk.owner and a.r_constraint_name = c_pk.constraint_name
        where a.constraint_type = 'R'
          and a.owner = :owner
          and c_pk.owner = :owner
        """,
        {"owner": owner},
    )
    edges = []
    for child, parent in cur.fetchall():
        if child in tables and parent in tables and child != parent:
            edges.append((parent, child))
    return edges


def _topo_sort(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    nodes = list(dict.fromkeys(tables))
    indeg = {t: 0 for t in nodes}
    outs: dict[str, list[str]] = {t: [] for t in nodes}
    for parent, child in edges:
        if parent in indeg and child in indeg:
            outs[parent].append(child)
            indeg[child] += 1
    queue = [t for t in nodes if indeg[t] == 0]
    ordered: list[str] = []
    while queue:
        n = queue.pop(0)
        ordered.append(n)
        for m in outs[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    leftover = [t for t in nodes if t not in ordered]
    return ordered + leftover


def classify_retry(source_count: int | None, target_count: int | None, limit: int = ROW_LIMIT) -> str:
    """Decide whether a table still needs copying.

    Counts may be capped at limit+1, which means "more than limit rows".
    A target that already has the full table, or at least `limit` rows, is left unchanged.
    """
    if source_count is None:
        return "missing_source"
    if target_count is None:
        return "missing_target"
    needed = source_count if source_count <= limit else limit
    if target_count > needed:
        return "target_ahead"
    if target_count == needed:
        return "complete"
    return "incomplete"


def _is_temporal(data_type: str) -> bool:
    dtype = (data_type or "").upper()
    return dtype == "DATE" or dtype.startswith("TIMESTAMP")


def _is_numeric(data_type: str) -> bool:
    dtype = (data_type or "").upper()
    return dtype in {"NUMBER", "FLOAT", "INTEGER", "INT", "BINARY_DOUBLE", "BINARY_FLOAT"} or dtype.startswith("NUMBER")


def choose_order_column(
    columns: list[dict[str, Any]],
    pk_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Pick a column that can identify the latest rows, without assuming one date name."""
    by_name = {str(col.get("name") or "").upper(): col for col in columns if col.get("name")}

    def usable(name: str) -> bool:
        col = by_name.get(name)
        if not col or name in _UNRELIABLE_KEY_NAMES:
            return False
        dtype = str(col.get("dataType") or "")
        return _is_temporal(dtype) or _is_numeric(dtype)

    def pick(names: tuple[str, ...], reason: str) -> dict[str, Any] | None:
        for name in names:
            if usable(name):
                return {"columns": [name], "reliable": True, "reason": reason}
        return None

    found = pick(_MODIFY_NAMES, "modification column") or pick(_CREATE_NAMES, "creation column")
    if found:
        return found

    hinted: list[tuple[int, str]] = []
    for name, col in by_name.items():
        if not usable(name):
            continue
        if any(hint in name for hint in _ORDER_NAME_HINTS):
            hinted.append((0 if _is_temporal(str(col.get("dataType") or "")) else 1, name))
    if hinted:
        hinted.sort()
        return {"columns": [hinted[0][1]], "reliable": True, "reason": "date or version column"}

    pk = [str(name).upper() for name in (pk_columns or []) if str(name).upper() in by_name]
    if pk and all(usable(name) for name in pk):
        return {"columns": pk, "reliable": True, "reason": "primary key"}

    return {"columns": [], "reliable": False, "reason": "no date, version, sequence, or key column"}


def _primary_key_columns(conn, schema: str, table: str) -> list[str]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    cur = conn.cursor()
    cur.execute(
        """
        select cc.column_name
        from all_constraints c
        join all_cons_columns cc
          on c.owner = cc.owner
         and c.constraint_name = cc.constraint_name
        where c.owner = :owner
          and c.table_name = :table_name
          and c.constraint_type = 'P'
        order by cc.position
        """,
        {"owner": owner, "table_name": table_name},
    )
    return [str(row[0]).upper() for row in cur.fetchall()]


def _row_count(conn, schema: str, table: str, cap: int = ROW_LIMIT + 1) -> int | None:
    """Count rows only up to cap. cap means the table has at least that many rows."""
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    cur = conn.cursor()
    cur.execute(
        """
        select 1
        from all_tables
        where owner = :owner and table_name = :table_name
        """,
        {"owner": owner, "table_name": table_name},
    )
    if cur.fetchone() is None:
        return None
    cur.execute(
        f"select count(*) from (select 1 from {owner}.{table_name} where rownum <= :cap)",
        {"cap": int(cap)},
    )
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _create_table(conn, schema: str, table: str, columns: list[dict[str, Any]]) -> list[str]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    ordered = sorted(columns, key=lambda col: col.get("columnId") or 0)
    if not ordered:
        raise ValueError(f"{owner}.{table_name} has no columns to create")
    defs = ", ".join(_column_ddl(col, force_null=False) for col in ordered)
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE {owner}.{table_name} ({defs})")
    conn.commit()
    return [col["name"] for col in ordered]


def _add_missing_columns(conn, schema: str, table: str, missing: list[dict[str, Any]]) -> list[str]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    cur = conn.cursor()
    applied = []
    for col in missing:
        ddl = _column_ddl(col)
        cur.execute(f"ALTER TABLE {owner}.{table_name} ADD ({ddl})")
        applied.append(col["name"])
    conn.commit()
    return applied


def _copy_table(src_conn, tgt_conn, schema: str, table: str, *, replace_data: bool, batch_size: int = 500, on_rows=None) -> dict[str, Any]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    src_cols = _columns(src_conn, owner, table_name)
    tgt_cols = {c["name"] for c in _columns(tgt_conn, owner, table_name)}
    shared = [c["name"] for c in src_cols if c["name"] in tgt_cols]
    if not shared:
        raise ValueError(f"{owner}.{table_name} has no shared columns to copy")
    col_list = ", ".join(shared)
    placeholders = ", ".join(f":{i + 1}" for i in range(len(shared)))
    order = choose_order_column(src_cols, _primary_key_columns(src_conn, owner, table_name))
    order_cols = order["columns"]
    if order_cols:
        order_sql = ", ".join(f"{_quote_ident(name)} DESC NULLS LAST" for name in order_cols)
        inner_cols = list(shared)
        for name in order_cols:
            if name not in inner_cols:
                inner_cols.append(name)
        inner_list = ", ".join(inner_cols)
        select_sql = f"""
            SELECT {col_list} FROM (
                SELECT {inner_list}
                FROM {owner}.{table_name}
                ORDER BY {order_sql}
            ) WHERE ROWNUM <= :row_limit
        """
    else:
        LOG.warning(
            "%s.%s has no reliable order column (%s); copying up to %s rows without ORDER BY",
            owner,
            table_name,
            order["reason"],
            ROW_LIMIT,
        )
        select_sql = f"SELECT {col_list} FROM {owner}.{table_name} WHERE ROWNUM <= :row_limit"
    src = src_conn.cursor()
    tgt = tgt_conn.cursor()
    deleted = 0
    if replace_data:
        tgt.execute(f"DELETE FROM {owner}.{table_name}")
        deleted = tgt.rowcount if tgt.rowcount and tgt.rowcount > 0 else 0
    src.execute(select_sql, {"row_limit": ROW_LIMIT})
    inserted = 0
    while True:
        rows = src.fetchmany(batch_size)
        if not rows:
            break
        tgt.executemany(
            f"INSERT INTO {owner}.{table_name} ({col_list}) VALUES ({placeholders})",
            rows,
        )
        inserted += len(rows)
        if on_rows:
            on_rows(inserted, deleted)
    tgt_conn.commit()
    return {
        "deleted": deleted,
        "inserted": inserted,
        "columns": shared,
        "rowLimit": ROW_LIMIT,
        "orderColumn": ", ".join(order_cols),
        "orderReliable": bool(order["reliable"]),
        "orderReason": order["reason"],
    }


def start_sync(
    owner: str | None,
    *,
    source: OracleEndpoint,
    target: OracleEndpoint,
    schema: str,
    tables: list[str],
    add_missing_columns: bool = True,
    create_missing_tables: bool = True,
    replace_data: bool = False,
    retry_failed: bool = False,
) -> None:
    job = _job(owner)
    with _lock:
        if job.running:
            raise ValueError("A DB sync is already running for your session.")
        previous = list(job.selected_tables) or _load_selection(owner)
        chosen = [_quote_ident(t) for t in (tables or previous)]
        if not chosen:
            raise ValueError("Select at least one table, or retry after a finished sync.")
        _save_selection(owner, schema, chosen)
        job.running = True
        job.cancel_requested = False
        job.phase = "starting"
        job.current_table = ""
        job.current_action = "starting"
        job.message = "Checking which tables still need to be copied..." if retry_failed else "Starting DB sync..."
        job.completed = 0
        job.total = len(chosen)
        job.rows_copied = 0
        job.selected_tables = chosen
        job.results = []
        job.compare = None
    threading.Thread(
        target=_run_sync,
        args=(
            owner,
            source,
            target,
            schema,
            chosen,
            add_missing_columns,
            create_missing_tables,
            replace_data,
            retry_failed,
        ),
        name="db-sync",
        daemon=True,
    ).start()


def _run_sync(
    owner: str | None,
    source: OracleEndpoint,
    target: OracleEndpoint,
    schema: str,
    tables: list[str],
    add_missing_columns: bool,
    create_missing_tables: bool,
    replace_data: bool,
    retry_failed: bool = False,
) -> None:
    job = _job(owner)

    def note(action: str, table: str, index: int, total: int, *, rows: int = 0) -> bool:
        with _lock:
            job.phase = action
            job.current_action = action
            job.current_table = f"{schema_name}.{table}" if table else ""
            job.completed = max(0, index - 1)
            job.total = total
            job.rows_copied = rows
            label = job.current_table or "schemas"
            stop = "Stop requested — " if job.cancel_requested else ""
            extra = f", {rows} row(s) copied" if rows else ""
            job.message = f"{stop}{action} {index}/{total}: {label}{extra}"
            return not job.cancel_requested

    try:
        schema_name = _quote_ident(schema)
        selected = [_quote_ident(t) for t in tables]
        replace_partial: set[str] = set()
        if retry_failed:
            note("checking", "", 0, len(selected))
            pending: list[str] = []
            with _connect(source) as src_conn, _connect(target) as tgt_conn:
                for index, table in enumerate(selected, start=1):
                    if not note("checking", table, index, len(selected)):
                        with _lock:
                            job.message = (
                                f"Stopped while checking {schema_name}.{table} ({index - 1}/{len(selected)})"
                            )
                        return
                    kind = classify_retry(
                        _row_count(src_conn, schema_name, table),
                        _row_count(tgt_conn, schema_name, table),
                    )
                    if kind in {"complete", "target_ahead"}:
                        message = (
                            "Already fully synced (row counts match). Left unchanged."
                            if kind == "complete"
                            else "Target already has at least as many rows as source. Left unchanged."
                        )
                        with _lock:
                            job.results.append(
                                {"table": table, "success": True, "skipped": True, "message": message}
                            )
                            job.completed = index
                            job.message = f"Skipping {schema_name}.{table} ({index}/{len(selected)}): {message}"
                        continue
                    if kind == "missing_source":
                        with _lock:
                            job.results.append(
                                {
                                    "table": table,
                                    "success": False,
                                    "message": "Table does not exist on source.",
                                }
                            )
                            job.completed = index
                        continue
                    if kind == "incomplete":
                        replace_partial.add(table)
                    pending.append(table)
            if not pending:
                with _lock:
                    skipped = sum(1 for row in job.results if row.get("skipped"))
                    failed = sum(1 for row in job.results if not row.get("success"))
                    job.current_action = "finished"
                    job.completed = len(selected)
                    job.message = f"Nothing to retry. {skipped} already complete and left unchanged, {failed} failed."
                return
            selected = pending

        note("comparing", "", 0, len(selected))

        def on_compare(index: int, total: int, table: str) -> bool:
            return note("comparing", table, index, total)

        compare = compare_tables(source, target, schema_name, selected, on_table=on_compare)
        with _lock:
            job.compare = compare
            stopped = job.cancel_requested
        if stopped:
            with _lock:
                job.message = f"Stopped during compare at {job.current_table or 'start'} ({job.completed}/{job.total})"
            return
        with _connect(source) as src_conn, _connect(target) as tgt_conn:
            edges = _fk_edges(src_conn, schema_name, set(selected))
            ordered = _topo_sort(selected, edges)
            for index, table in enumerate(ordered, start=1):
                if not note("syncing", table, index, len(ordered)):
                    with _lock:
                        job.message = f"Stopped before {schema_name}.{table} ({index - 1}/{len(ordered)})"
                    break
                table_cmp = next((t for t in compare["tables"] if t["table"] == table), None)
                if table_cmp and table_cmp.get("status") == "missing_on_source":
                    result = {
                        "table": table,
                        "success": False,
                        "addedColumns": [],
                        "message": "Table does not exist on source.",
                    }
                    with _lock:
                        job.results.append(result)
                        job.completed = index
                        job.message = f"Skipped {schema_name}.{table} ({index}/{len(ordered)}): {result['message']}"
                    continue
                if table_cmp and table_cmp.get("status") == "missing_on_target" and not create_missing_tables:
                    result = {
                        "table": table,
                        "success": False,
                        "addedColumns": [],
                        "message": "Table does not exist on target. Enable Create missing tables on DEV.",
                    }
                    with _lock:
                        job.results.append(result)
                        job.completed = index
                        job.message = f"Skipped {schema_name}.{table} ({index}/{len(ordered)}): {result['message']}"
                    continue

                def on_rows(inserted: int, _deleted: int, _table=table, _index=index, _total=len(ordered)) -> None:
                    note("syncing", _table, _index, _total, rows=inserted)

                added: list[str] = []
                created = False
                try:
                    if table_cmp and table_cmp.get("status") == "missing_on_target":
                        note("creating table", table, index, len(ordered))
                        added = _create_table(tgt_conn, schema_name, table, table_cmp.get("missingColumns") or [])
                        created = True
                    elif add_missing_columns and table_cmp and table_cmp.get("missingColumns"):
                        note("adding columns", table, index, len(ordered))
                        added = _add_missing_columns(tgt_conn, schema_name, table, table_cmp["missingColumns"])
                    note("syncing", table, index, len(ordered))
                    stats = _copy_table(
                        src_conn,
                        tgt_conn,
                        schema_name,
                        table,
                        replace_data=table in replace_partial if retry_failed else replace_data,
                        on_rows=on_rows,
                    )
                    detail = f"Inserted {stats['inserted']} row(s)"
                    if stats.get("orderColumn"):
                        detail += f", latest by {stats['orderColumn']} (max {stats['rowLimit']})"
                    elif stats.get("orderReliable") is False:
                        detail += f", first {stats['inserted']} rows only; no reliable order column"
                    if stats["deleted"]:
                        detail += f", deleted {stats['deleted']} partial row(s) before reload"
                    if created:
                        detail += f", created table with {len(added)} column(s)"
                    elif added:
                        detail += f", added columns {', '.join(added)}"
                    result = {
                        "table": table,
                        "success": True,
                        "addedColumns": added,
                        **stats,
                        "message": detail,
                    }
                except Exception as exc:
                    LOG.exception("DB sync failed for %s.%s", schema_name, table)
                    result = {"table": table, "success": False, "addedColumns": added, "message": str(exc)}
                with _lock:
                    job.results.append(result)
                    job.completed = index
                    job.current_table = f"{schema_name}.{table}"
                    job.message = f"Finished {schema_name}.{table} ({index}/{len(ordered)}): {result['message']}"
        with _lock:
            if not job.cancel_requested:
                copied = sum(1 for row in job.results if row.get("success") and not row.get("skipped"))
                skipped = sum(1 for row in job.results if row.get("skipped"))
                failed = sum(1 for row in job.results if not row.get("success"))
                job.current_action = "finished"
                if retry_failed:
                    job.message = (
                        f"Retry finished: {copied} copied, {skipped} left unchanged, {failed} failed"
                    )
                else:
                    job.message = f"Finished: {copied} succeeded, {failed} failed"
    except Exception as exc:
        LOG.exception("DB sync failed")
        with _lock:
            where = f" on {job.current_table}" if job.current_table else ""
            job.message = f"DB sync failed{where}: {exc}"
            job.results.append({"table": job.current_table or "*", "success": False, "message": str(exc)})
    finally:
        with _lock:
            job.running = False
            job.cancel_requested = False
            job.phase = "idle"
            job.current_action = job.current_action if job.current_action == "finished" else "idle"
