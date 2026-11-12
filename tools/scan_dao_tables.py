#!/usr/bin/env python3
"""Scan the Bob monolith's jOOQ table definitions + DAOs and write one skill per module.

    python tools/scan_dao_tables.py ~/IdeaProjects/hibob [--check-db http://127.0.0.1:8137]

* Table definitions: ``class XTable(...) extends JooqTable(...)`` with ``createXxxField("column")`` fields.
* Physical table names: ``extends JooqTable("name")`` or the literals passed where the class is instantiated.
* DAOs: every ``class *Dao`` / ``*Repository`` file that references a table class.
Output (git-ignored): knowledge_local/models/<module>/<module>.md (the skill) and tables.json.
``--check-db`` marks tables that exist in the local ``hibob`` database via the running db-dog API.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "knowledge_local" / "models"
PAREN = r"\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
CLASS_RE = re.compile(r"\bclass\s+(\w+)\s*(?:\[[^\]]*\])?\s*(" + PAREN + r")?\s*extends\s+(?:JooqTable|BobJooqTable|OrgJooqTable)\s*(" + PAREN + r")?[^{=]*\{", re.S)
FIELD_RE = re.compile(r'(?:val|lazy val|def)\s+(\w+)\s*:\s*TableField\[([^\]]+)\]\s*=\s*create(\w*?)Field\(\s*"([^"]+)"')
FIELD2_RE = re.compile(r'create(\w*?)Field\(\s*"([^"]+)"')
DAO_RE = re.compile(r"\bclass\s+(\w*(?:Dao|DAO|Repository|Repo))\b")
SQL_RE = re.compile(r"\b(?:from|join|into|update)\s+([a-z][a-z0-9_]+)\b", re.I)
TYPE = {"BigInt": "bigint", "Varchar": "varchar", "Json": "jsonb", "Boolean": "boolean", "Text": "text", "Instant": "timestamp",
        "Timestamp": "timestamp", "LocalDateTime": "timestamp", "LocalDate": "date", "Integer": "integer",
        "VarcharArray": "varchar[]", "BigDecimal": "numeric", "Double": "double", "Real": "real", "SLong": "bigint", "SBoolean": "boolean"}


def module_of(path: Path, base: Path) -> str:
    rel = path.relative_to(base).parts
    return rel[0] if len(rel) > 1 else "_root"


def block(text: str, start: int) -> str:
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start:i]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 1:
        print(__doc__)
        return 2
    root = Path(args[0]).expanduser().resolve()
    base = root / "app" / "com" / "hibob"
    check = sys.argv[sys.argv.index("--check-db") + 1] if "--check-db" in sys.argv else None
    files = [p for p in base.rglob("*.scala") if "/test/" not in str(p)]
    texts = {p: p.read_text(errors="replace") for p in files}

    classes: dict[str, dict] = {}      # class name -> info
    for p, t in texts.items():
        for m in CLASS_RE.finditer(t):
            name, params, super_args = m.group(1), m.group(2) or "", (m.group(3) or "")[1:-1]
            dflt = re.search(r'=\s*"([a-z][a-z0-9_]+)"', params)
            body = block(t, m.end())
            fields = [(f.group(4), TYPE.get(f.group(3), f.group(3).lower() or "?")) for f in FIELD_RE.finditer(body)]
            seen = {c for c, _ in fields}
            fields += [(c, TYPE.get(k, k.lower() or "?")) for k, c in FIELD2_RE.findall(body) if c not in seen and not seen.add(c)]
            lit = re.match(r'\s*(?:name\s*=\s*)?"([^"]+)"', super_args)
            classes[name] = {"file": p, "fields": fields, "tables": ({lit.group(1)} if lit else set()) | ({dflt.group(1)} if dflt else set())}

    # physical names from instantiations
    inst = re.compile(r'(?:new\s+)?(\w+)\(\s*(?:\w+\s*=\s*)?"([a-z][a-z0-9_]+)"')
    for t in texts.values():
        for cls, tbl in inst.findall(t):
            if cls in classes:
                classes[cls]["tables"].add(tbl)

    # DAOs referencing each table class
    daos: dict[str, set[str]] = defaultdict(set)
    for p, t in texts.items():
        dnames = DAO_RE.findall(t)
        if not dnames:
            continue
        for cls in classes:
            if re.search(rf"\b{cls}\b", t):
                daos[cls].update(dnames)

    tables: dict[str, dict] = {}
    for cls, info in classes.items():
        for tbl in sorted(info["tables"]):
            mod = module_of(info["file"], base)
            e = tables.setdefault(tbl, {"table": tbl, "module": mod, "class": cls, "file": str(info["file"].relative_to(root)),
                                        "columns": info["fields"], "daos": sorted(daos.get(cls, []))})
            if len(info["fields"]) > len(e["columns"]):
                e.update(columns=info["fields"], class_=cls)

    # raw-SQL tables referenced by DAOs but without a jOOQ definition
    extra: dict[str, set[str]] = defaultdict(set)
    for p, t in texts.items():
        if DAO_RE.search(t):
            for tbl in SQL_RE.findall(t):
                if "_" in tbl and tbl not in tables and tbl.islower():
                    extra[module_of(p, base)].add(tbl)

    existing: set[str] | None = None
    if check:
        q = {"environment": "local", "store": "postgres", "database": "hibob",
             "statement": "select table_name from information_schema.tables where table_schema='public'"}
        req = urllib.request.Request(check + "/api/query", json.dumps(q).encode(), {"content-type": "application/json"})
        existing = {r[0] for r in json.load(urllib.request.urlopen(req, timeout=60))["rows"]}

    by_mod: dict[str, list[dict]] = defaultdict(list)
    for e in tables.values():
        by_mod[e["module"]].append(e)
    OUT.mkdir(parents=True, exist_ok=True)
    for mod, ts in sorted(by_mod.items()):
        d = OUT / mod
        d.mkdir(exist_ok=True)
        ts.sort(key=lambda e: e["table"])
        (d / "tables.json").write_text(json.dumps(ts, indent=1))
        lines = [f"<!-- generated by tools/scan_dao_tables.py from app/com/hibob/{mod}/ -->", f"# Tables of module `{mod}` (from its jOOQ DAOs)",
                 "Source: jOOQ table definitions in the monolith. Types are the jOOQ field kinds; the live schema wins if it differs.",
                 "Tenant data: filter `company_id`; `employee_id` is a string. Tables are in the Postgres database `hibob` unless a note says otherwise.",
                 f"Tables: {', '.join('`' + e['table'] + '`' for e in ts)}\n"]
        for e in ts:
            cols = ", ".join(f"{c} {t}" for c, t in e["columns"])
            tag = "" if existing is None else (" [exists in local hibob]" if e["table"] in existing else " [not in local hibob]")
            rel = [c for c, _ in e["columns"] if c.endswith("_id") and c not in ("company_id", "employee_id")]
            lines.append(f"## {e['table']}{tag}\n- columns: {cols or '(none parsed)'}\n- table class: `{e['class']}` (`{e['file']}`)"
                         + (f"\n- DAOs: {', '.join(e['daos'])}" if e["daos"] else "")
                         + (f"\n- reference columns: {', '.join(rel)}" if rel else ""))
        if extra.get(mod):
            lines.append("## Other tables seen in raw SQL of this module's DAOs\n" + ", ".join(f"`{t}`" for t in sorted(extra[mod])))
        (d / f"{mod}.md").write_text("\n".join(lines) + "\n")
    if existing is not None:   # tables that exist locally but have no jOOQ definition (audit/trigger tables, other services)
        orphan = sorted(existing - set(tables))
        d = OUT / "_unmapped"
        d.mkdir(exist_ok=True)
        (d / "_unmapped.md").write_text("<!-- generated by tools/scan_dao_tables.py -->\n# Tables in local hibob with no jOOQ table class in the monolith\n"
                                        "Mostly *_audit tables (written by DB triggers), migration/stage tables and tables owned by other code. "
                                        "Columns: describe_table.\n" + ", ".join(f"`{t}`" for t in orphan) + "\n")
    total = sum(len(v) for v in by_mod.values())
    print(f"{len(classes)} table classes, {total} tables, {len(by_mod)} modules -> {OUT}")
    if existing is not None:
        missing = [e["table"] for e in tables.values() if e["table"] not in existing]
        print(f"{total - len(missing)} exist in local hibob, {len(missing)} not")
    for mod, ts in sorted(by_mod.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"  {mod:<20} {len(ts):>3} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
