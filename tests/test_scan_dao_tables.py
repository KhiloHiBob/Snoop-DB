from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCALA = '''package com.hibob.demo
class DemoThingTable(tableName: String, aliased: Option[DemoThingTable] = None) extends JooqTable(tableName, aliased) {
  val id: TableField[JLong] = createBigIntField("id")
  val companyId: TableField[JLong] = createBigIntField("company_id")
  val payload: TableField[JsValue] = createJsonField("payload")
}
object DemoThingTable { def apply() = new DemoThingTable("demo_things") }
class DemoDao @Inject()(jooq: Jooq) { private val t = DemoThingTable() }
class FixedTable extends JooqTable("fixed_table") { val id = createBigIntField("id") }
'''


def test_scan_finds_tables_columns_and_daos(tmp_path, monkeypatch):
    f = tmp_path / "app" / "com" / "hibob" / "demo" / "DemoDao.scala"
    f.parent.mkdir(parents=True)
    f.write_text(SCALA)
    spec = importlib.util.spec_from_file_location("scan", Path(__file__).parent.parent / "tools" / "scan_dao_tables.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    monkeypatch.setattr(scan, "OUT", tmp_path / "out")
    monkeypatch.setattr(sys, "argv", ["scan", str(tmp_path)])
    assert scan.main() == 0
    tables = {t["table"]: t for t in json.loads((tmp_path / "out" / "demo" / "tables.json").read_text())}
    assert set(tables) == {"demo_things", "fixed_table"}
    assert [c for c, _ in tables["demo_things"]["columns"]] == ["id", "company_id", "payload"]
    assert tables["demo_things"]["daos"] == ["DemoDao"]
    assert "demo_things" in (tmp_path / "out" / "demo" / "demo.md").read_text()
