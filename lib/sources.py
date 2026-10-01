"""
Load every configured source into DuckDB as a view with the standard column names and types.

Two kinds of location are supported:
  type: file  - a path or glob to CSV, JSON / JSON Lines (optionally .gz) or Parquet files
  type: sql   - a database the team can query: a SQLAlchemy URL plus a SELECT query
                (Postgres, MySQL, Snowflake, BigQuery, Redshift, SQLite... whatever driver is installed)

Each source becomes a view named src_<source> (src_usage, src_accounts, ...). Fields the team
didn't map come through as typed NULL columns so the SQL never has to care which ones exist.
"""

import os
from pathlib import Path

from . import contract as C

SQL_TYPES = {}
for f in C.NUMERIC_FIELDS:
    SQL_TYPES[f] = "DOUBLE"
for f in C.DATE_FIELDS:
    SQL_TYPES[f] = "DATE"
for f in C.TIMESTAMP_FIELDS:
    SQL_TYPES[f] = "TIMESTAMP"
for f in C.MONTH_FIELDS:
    SQL_TYPES[f] = "DATE"
SQL_TYPES["status"] = "INTEGER"
SQL_TYPES["payment_method_added"] = "BOOLEAN"


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _reader(path, fmt=None):
    p = str(path).lower()
    fmt = fmt or ("parquet" if ".parquet" in p else "json" if (".json" in p) else "csv")
    path_sql = "'" + str(path).replace("'", "''") + "'"
    if fmt == "parquet":
        return f"read_parquet({path_sql}, union_by_name = true)"
    if fmt == "json":
        return f"read_json_auto({path_sql}, union_by_name = true, maximum_object_size = 104857600)"
    return f"read_csv_auto({path_sql}, header = true, union_by_name = true)"


def raw_relation(con, name, spec, base_dir="."):
    """Register the raw (unmapped) source as raw_<name> and return its column names."""
    kind = spec.get("type", "file")
    if kind == "file":
        path = spec["path"]
        if not os.path.isabs(path) and not path.startswith(("s3://", "gs://", "http")):
            path = str(Path(base_dir) / path)
        con.execute(f"CREATE OR REPLACE VIEW raw_{name} AS SELECT * FROM {_reader(path, spec.get('format'))}")
    elif kind == "sql":
        import pandas as pd
        from sqlalchemy import create_engine, text
        url = os.path.expandvars(spec["url"])  # lets the URL read secrets from env vars, e.g. ${WAREHOUSE_URL}
        engine = create_engine(url)
        with engine.connect() as c:
            df = pd.read_sql(text(spec["query"]), c)
        con.register(f"df_{name}", df)
        con.execute(f"CREATE OR REPLACE TABLE raw_{name} AS SELECT * FROM df_{name}")
    else:
        raise ValueError(f"{name}: unknown source type '{kind}' (use 'file' or 'sql')")
    return [r[0] for r in con.execute(f"DESCRIBE raw_{name}").fetchall()]


def _cast(field, col):
    t = SQL_TYPES.get(field, "VARCHAR")
    c = _q(col)
    if field in C.MONTH_FIELDS:
        return (f"date_trunc('month', COALESCE(TRY_CAST({c} AS DATE), "
                f"TRY_CAST(CAST({c} AS VARCHAR) || '-01' AS DATE)))::DATE")
    if field in C.TIMESTAMP_FIELDS:
        # Naive timestamps are read as UTC; the session time zone is UTC.
        return f"TRY_CAST({c} AS TIMESTAMPTZ)"
    if t == "BOOLEAN":
        return f"TRY_CAST({c} AS BOOLEAN)"
    return f"TRY_CAST({c} AS {t})"


def _null(field):
    t = SQL_TYPES.get(field, "VARCHAR")
    if field in C.TIMESTAMP_FIELDS:
        t = "TIMESTAMPTZ"
    return f"CAST(NULL AS {t})"


def standard_view(con, name, spec, base_dir="."):
    """Create src_<name> with standard columns. Returns (grain, missing_required)."""
    cols = raw_relation(con, name, spec, base_dir)
    mapping = {k: v for k, v in (spec.get("columns") or {}).items() if v}
    mapped = set(mapping)
    grain = None
    if name == "usage":
        grain = spec.get("grain") or C.detect_grain(mapped)
        if grain is None:
            return None, ["ts or date"]
    req, opt = C.fields_for(name, grain)
    missing = [f for f in req if f not in mapping]
    bad = [f"{f} -> '{c}'" for f, c in mapping.items() if c not in cols]
    if bad:
        raise ValueError(f"{name}: mapped columns not found in the data: {', '.join(bad)}. Columns available: {cols}")
    if missing:
        return grain, missing
    parts = []
    for f in req + opt:
        parts.append(f"{_cast(f, mapping[f]) if f in mapping else _null(f)} AS {f}")
    con.execute(f"CREATE OR REPLACE VIEW src_{name} AS SELECT {', '.join(parts)} FROM raw_{name}")
    return grain, []


def empty_view(con, name, grain=None):
    """A typed, empty src_<name> for optional sources the team doesn't have."""
    req, opt = C.fields_for(name, grain or "call")
    parts = [f"{_null(f)} AS {f}" for f in req + opt]
    con.execute(f"CREATE OR REPLACE VIEW src_{name} AS SELECT {', '.join(parts)} WHERE FALSE")


def load_all(con, cfg, base_dir="."):
    """Load every source. Returns dict of {source: 'loaded'|'empty'} and the usage grain."""
    con.execute("SET TimeZone = 'UTC'")
    status, grain = {}, None
    for name, spec in C.SOURCES.items():
        s = (cfg.get("sources") or {}).get(name)
        if not s or s.get("skip"):
            if spec["required"]:
                raise ValueError(f"Required source '{name}' is not configured. Run: python jevclose.py setup")
            empty_view(con, name, "daily" if name == "usage" else None)
            status[name] = "empty"
            continue
        g, missing = standard_view(con, name, s, base_dir)
        if missing:
            raise ValueError(f"{name}: required fields not mapped: {', '.join(missing)}. Run: python jevclose.py setup")
        if name == "usage":
            grain = g
        status[name] = "loaded"
    return status, grain
