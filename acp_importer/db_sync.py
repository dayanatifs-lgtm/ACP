"""CFG to DEV Oracle schema compare and data sync.

Not OS Data Pump (expdp/impdp). Uses python-oracledb to:
1) compare tables/columns
2) optionally ADD missing nullable columns on target
3) copy rows in FK-aware order where possible

Passwords are never written to disk by this module.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

LOG = logging.getLogger("DbSync")

IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")


@dataclass
class OracleEndpoint:
    host: str
    port: int = 1521
    service: str = ""
    user: str = ""
    password: str = ""
    connect_as: str = "service"  # "service" | "sid"

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
    completed: int = 0
    total: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    compare: dict[str, Any] | None = None


_jobs: dict[str, SyncJob] = {}
_lock = threading.RLock()


def _job(owner: str | None) -> SyncJob:
    key = (owner or "anonymous").strip().lower() or "anonymous"
    with _lock:
        if key not in _jobs:
            _jobs[key] = SyncJob()
        return _jobs[key]


def get_status(owner: str | None) -> dict[str, Any]:
    job = _job(owner)
    with _lock:
        return asdict(job)


def request_stop(owner: str | None) -> None:
    job = _job(owner)
    with _lock:
        if not job.running:
            raise ValueError("No DB sync is currently running.")
        job.cancel_requested = True
        job.message = "Stop requested..."


def _require_oracledb():
    try:
        import oracledb
    except ImportError as exc:
        raise ValueError(
            "Python package 'oracledb' is not installed. Run: pip install oracledb"
        ) from exc
    return oracledb


def _quote_ident(name: str) -> str:
    text = (name or "").strip()
    if not text or not IDENTIFIER_RE.match(text):
        raise ValueError(f"Invalid Oracle identifier: {name!r}")
    return text.upper()


def _connect(endpoint: OracleEndpoint):
    oracledb = _require_oracledb()
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
    return oracledb.connect(**kwargs)


def _friendly_connect_error(exc: Exception, endpoint: OracleEndpoint) -> str:
    text = str(exc)
    mode = endpoint.connect_mode()
    other = "SID" if mode == "service" else "service name"
    hints = [
        f"Tried connect as {mode} to {endpoint.host}:{endpoint.port or 1521} / {endpoint.service}.",
        f"If this keeps failing, switch Connect as to {other}.",
        "Confirm the app server can reach the DB host on TCP 1521.",
        "If the DB uses Native Network Encryption, thick-mode Oracle Instant Client may be required.",
    ]
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


def _column_ddl(col: dict[str, Any]) -> str:
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
    return f"{name} {type_sql} NULL"


def compare_tables(
    source: OracleEndpoint,
    target: OracleEndpoint,
    schema: str,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    owner = _quote_ident(schema)
    with _connect(source) as src, _connect(target) as tgt:
        src_tables = set(list_tables(source, owner))
        tgt_tables = set(list_tables(target, owner))
        selected = [_quote_ident(t) for t in (tables or sorted(src_tables))]
        report = []
        for table in selected:
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


def _copy_table(src_conn, tgt_conn, schema: str, table: str, *, replace_data: bool, batch_size: int = 500) -> dict[str, Any]:
    owner = _quote_ident(schema)
    table_name = _quote_ident(table)
    src_cols = _columns(src_conn, owner, table_name)
    tgt_cols = {c["name"] for c in _columns(tgt_conn, owner, table_name)}
    shared = [c["name"] for c in src_cols if c["name"] in tgt_cols]
    if not shared:
        raise ValueError(f"{owner}.{table_name} has no shared columns to copy")
    col_list = ", ".join(shared)
    placeholders = ", ".join(f":{i + 1}" for i in range(len(shared)))
    src = src_conn.cursor()
    tgt = tgt_conn.cursor()
    deleted = 0
    if replace_data:
        tgt.execute(f"DELETE FROM {owner}.{table_name}")
        deleted = tgt.rowcount if tgt.rowcount and tgt.rowcount > 0 else 0
    src.execute(f"SELECT {col_list} FROM {owner}.{table_name}")
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
    tgt_conn.commit()
    return {"deleted": deleted, "inserted": inserted, "columns": shared}


def start_sync(
    owner: str | None,
    *,
    source: OracleEndpoint,
    target: OracleEndpoint,
    schema: str,
    tables: list[str],
    add_missing_columns: bool = True,
    replace_data: bool = False,
) -> None:
    job = _job(owner)
    with _lock:
        if job.running:
            raise ValueError("A DB sync is already running for your session.")
        job.running = True
        job.cancel_requested = False
        job.phase = "starting"
        job.message = "Starting DB sync..."
        job.completed = 0
        job.total = len(tables)
        job.results = []
        job.compare = None
    threading.Thread(
        target=_run_sync,
        args=(owner, source, target, schema, tables, add_missing_columns, replace_data),
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
    replace_data: bool,
) -> None:
    job = _job(owner)
    try:
        schema_name = _quote_ident(schema)
        selected = [_quote_ident(t) for t in tables]
        with _lock:
            job.phase = "compare"
            job.message = "Comparing source and target schemas..."
        compare = compare_tables(source, target, schema_name, selected)
        with _lock:
            job.compare = compare
        missing_target_tables = [t["table"] for t in compare["tables"] if t["status"] == "missing_on_target"]
        if missing_target_tables:
            raise ValueError(
                "These tables exist on source but not on target (create them first): "
                + ", ".join(missing_target_tables)
            )
        with _connect(source) as src_conn, _connect(target) as tgt_conn:
            edges = _fk_edges(src_conn, schema_name, set(selected))
            ordered = _topo_sort(selected, edges)
            with _lock:
                job.total = len(ordered)
                job.phase = "sync"
                job.message = f"Copying {len(ordered)} table(s)..."
            for index, table in enumerate(ordered, start=1):
                with _lock:
                    if job.cancel_requested:
                        job.message = f"Stopped after {index - 1} of {len(ordered)} tables"
                        break
                    job.completed = index - 1
                    job.message = f"Syncing {index}/{len(ordered)}: {schema_name}.{table}"
                table_cmp = next(t for t in compare["tables"] if t["table"] == table)
                added: list[str] = []
                if add_missing_columns and table_cmp.get("missingColumns"):
                    added = _add_missing_columns(tgt_conn, schema_name, table, table_cmp["missingColumns"])
                try:
                    stats = _copy_table(src_conn, tgt_conn, schema_name, table, replace_data=replace_data)
                    result = {
                        "table": table,
                        "success": True,
                        "addedColumns": added,
                        **stats,
                        "message": f"Inserted {stats['inserted']} row(s)"
                        + (f", deleted {stats['deleted']}" if stats["deleted"] else "")
                        + (f", added columns {', '.join(added)}" if added else ""),
                    }
                except Exception as exc:
                    LOG.exception("DB sync failed for %s.%s", schema_name, table)
                    result = {"table": table, "success": False, "addedColumns": added, "message": str(exc)}
                with _lock:
                    job.results.append(result)
                    job.completed = index
        with _lock:
            if not job.cancel_requested:
                ok = sum(1 for r in job.results if r.get("success"))
                job.message = f"Finished: {ok} succeeded, {len(job.results) - ok} failed"
    except Exception as exc:
        LOG.exception("DB sync failed")
        with _lock:
            job.message = f"DB sync failed: {exc}"
            job.results.append({"table": "*", "success": False, "message": str(exc)})
    finally:
        with _lock:
            job.running = False
            job.cancel_requested = False
            job.phase = "idle"
