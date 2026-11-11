from snoop_db.validation import validate_sql


class Executor:
    def __init__(self, settings, dialect):
        self.settings = settings
        self.dialect = dialect

    def execute(self, sql, parameters, schema, explain=False):
        # Revalidate at the execution boundary, including requests from internal callers.
        validated = validate_sql(sql, parameters, schema, self.dialect.sqlglot_name)
        query, values = self.dialect.bind(validated.sql, validated.parameters)
        with self.dialect.read_session(self.settings) as conn:
            # EXPLAIN is server-controlled and never uses ANALYZE.
            if explain:
                cursor = conn.execute("EXPLAIN (FORMAT JSON) " + query, values)
            else:
                cursor = conn.execute(
                    "SELECT * FROM (" + query + ") AS snoop_result LIMIT %(snoop_limit)s",
                    {**values, "snoop_limit": self.settings.max_rows + 1},
                )
            rows = cursor.fetchmany(self.settings.max_rows + 1)
            return {
                "sql": validated.sql,
                "parameters": validated.parameters,
                "columns": [column.name for column in cursor.description],
                "rows": [list(row) for row in rows[:self.settings.max_rows]],
                "truncated": len(rows) > self.settings.max_rows,
                "explain": explain,
            }
