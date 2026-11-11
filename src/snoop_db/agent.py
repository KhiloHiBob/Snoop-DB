from snoop_db.dialects import get_dialect
from snoop_db.execution import Executor
from snoop_db.generation import get_provider


class Agent:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self.dialect = get_dialect(settings.dialect)
        self.provider = provider or get_provider(settings)
        self.executor = Executor(settings, self.dialect)

    def schema(self):
        return self.dialect.discover(self.settings)

    def chat(self, question, history):
        schema = self.schema()
        generation = self.provider.generate(question, history, schema)
        response = {"kind": generation.kind, "message": generation.message, "schema": schema}
        if generation.kind != "query":
            return response
        result = self.executor.execute(
            generation.sql, generation.parameters, schema, explain=generation.explain,
        )
        response["result"] = result
        if result["explain"]:
            response["message"] += (
                " Estimated plan only (no ANALYZE). Compare scan types, estimated rows, "
                "join methods and costs; costs are planner units, not elapsed time."
            )
        else:
            response["message"] += (
                f" Returned {len(result['rows'])} row(s)"
                + ("; more rows exist (result limit reached)." if result["truncated"] else ".")
                + " Results are displayed locally; no result interpretation was sent to an AI provider."
            )
        if self.settings.send_results_to_llm and hasattr(self.provider, "summarize"):
            response["message"] = self.provider.summarize(question, result)
        return response
