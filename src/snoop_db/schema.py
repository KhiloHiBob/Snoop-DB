def discover_postgres(dialect, settings):
    """Read catalog metadata only; never sample rows or forward schema comments."""
    schema = {}
    with dialect.read_session(settings) as conn:
        for qualified in settings.allowed_relations:
            namespace, name = qualified.split(".")
            relation = conn.execute("""
                SELECT c.oid, c.relkind FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                  AND c.relkind IN ('r', 'p', 'v', 'm')
                  AND has_table_privilege(c.oid, 'SELECT')
            """, (namespace, name)).fetchone()
            if not relation:
                raise ValueError("An allowed relation is missing or not SELECT-accessible")
            oid, kind = relation
            columns = conn.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull
                FROM pg_attribute a
                WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
            """, (oid,)).fetchall()
            constraints = conn.execute("""
                SELECT conname, contype, pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid = %s ORDER BY conname
            """, (oid,)).fetchall()
            indexes = conn.execute("""
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s ORDER BY indexname
            """, (namespace, name)).fetchall()
            schema[qualified] = {
                "kind": "view" if kind in {"v", "m"} else "table",
                "columns": [{"name": c, "type": t, "nullable": n} for c, t, n in columns],
                "constraints": [{"name": n, "type": t, "definition": d} for n, t, d in constraints],
                "indexes": [{"name": n, "definition": d} for n, d in indexes],
            }
    return schema
