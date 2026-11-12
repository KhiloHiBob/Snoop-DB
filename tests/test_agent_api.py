import json

import pytest
from fastapi.testclient import TestClient

from db_dog.agent import Agent
from db_dog.app import create_app

from .conftest import FakeHub, ScriptedLLM, final, tool_call


def make_agent(config, responses):
    hub = FakeHub(config)
    llm = ScriptedLLM(responses)
    return Agent(config, hub, llm), hub, llm


def tool_results(llm):
    """Tool-result payloads the LLM was sent, parsed."""
    out = []
    for call in llm.calls:
        for m in call["messages"]:
            if isinstance(m["content"], list):
                out += [json.loads(b["content"]) for b in m["content"] if b.get("type") == "tool_result"]
    return out


SQL = "SELECT department, count(*) AS n FROM employees WHERE company_id = 1 GROUP BY department"


def test_happy_path_returns_table_and_environment(config):
    agent, hub, llm = make_agent(config, [tool_call("run_sql", {"database": "demo", "sql": SQL, "title": "By dept"}),
                                          final("Two departments on **local**.")])
    out = agent.run("how many employees per department?")
    assert out["environment"] == "local" and not out["production"]
    assert out["summary"].startswith("Two departments")
    [res] = out["results"]
    assert res["title"] == "By dept" and res["columns"] == ["department", "n"] and res["row_count"] == 2
    assert hub.conns["local"].queries and "LIMIT 500" in hub.conns["local"].queries[0]


def test_rows_are_withheld_from_the_llm_by_default(config):
    agent, _, llm = make_agent(config, [tool_call("run_sql", {"database": "demo", "sql": SQL}), final("ok")])
    out = agent.run("count per department")
    [payload] = tool_results(llm)
    assert "Engineering" not in json.dumps(payload)
    assert payload["rows"].startswith("withheld")
    assert out["rows_sent_to_llm"] is False
    assert out["results"][0]["rows"][0] == ["Engineering", 24]   # but the UI still gets the real rows


def test_rows_visible_when_requested_in_non_prod(config):
    agent, _, llm = make_agent(config, [tool_call("run_sql", {"database": "demo", "sql": SQL}), final("ok")])
    out = agent.run("count per department", llm_sees_rows=True)
    assert tool_results(llm)[0]["rows"][0] == ["Engineering", 24]
    assert out["rows_sent_to_llm"] is True


def test_rows_never_visible_in_prod_unless_enabled(config):
    agent, _, llm = make_agent(config, [tool_call("run_sql", {"database": "demo", "sql": SQL}), final("ok")])
    out = agent.run("count per department in prod", llm_sees_rows=True)
    assert out["environment"] == "prod" and out["production"]
    assert out["rows_sent_to_llm"] is False
    assert "Engineering" not in json.dumps(tool_results(llm))
    assert any("disabled" in w for w in out["warnings"])


def test_prod_rows_allowed_only_with_explicit_config(config):
    config.llm["allow_prod_rows_to_llm"] = True
    agent, _, llm = make_agent(config, [tool_call("run_sql", {"database": "demo", "sql": SQL}), final("ok")])
    out = agent.run("count per department in prod", llm_sees_rows=True)
    assert out["rows_sent_to_llm"] is True


def test_prod_requires_company_id(config):
    agent, hub, llm = make_agent(config, [
        tool_call("run_sql", {"database": "demo", "sql": "SELECT * FROM employees"}), final("blocked")])
    out = agent.run("show employees in prod")
    [payload] = tool_results(llm)
    assert "company_id" in payload["error"]
    assert out["steps"][0]["status"] == "rejected"
    assert out["results"] == [] and hub.conns["prod"].queries == []


@pytest.mark.parametrize("env_words", ["", "in prod", "in staging", "in sandbox", "on payroll prod"])
def test_writes_are_rejected_in_every_environment(config, env_words):
    agent, hub, llm = make_agent(config, [
        tool_call("run_sql", {"database": "demo", "sql": "DELETE FROM employees WHERE company_id = 1"}), final("refused")])
    out = agent.run(f"delete the employees {env_words}")
    assert out["steps"][0]["status"] == "rejected"
    assert all(not c.queries for c in hub.conns.values())


def test_environment_selector_overrides_wording(config):
    agent, *_ = make_agent(config, [final("hi")])
    assert agent.run("count rows in prod", environment="staging")["environment"] == "staging"


def test_ambiguous_environment_is_warned(config):
    agent, *_ = make_agent(config, [final("hi")])
    out = agent.run("compare staging and prod")
    assert out["environment"] == "prod" and any("Several environments" in w for w in out["warnings"])


def test_system_prompt_states_environment_and_privacy(config):
    agent, _, llm = make_agent(config, [final("hi")])
    agent.run("hello in prod")
    system = llm.calls[0]["system"]
    assert "**prod**" in system and "PRODUCTION" in system and "NOT visible" in system
    assert "company_id" in system                          # knowledge files are included
    assert "redis" not in " ".join(t["name"] for t in llm.calls[0]["tools"])   # prod has no redis store


def test_only_configured_stores_get_tools(config):
    agent, _, llm = make_agent(config, [final("hi")])
    agent.run("hello")  # local has redis configured
    names = {t["name"] for t in llm.calls[0]["tools"]}
    assert {"run_sql", "redis_read", "fan_out_query"} <= names and "mongo_find" not in names


def test_unavailable_store_is_reported_to_the_llm_not_raised(config):
    agent, *_ = make_agent(config, [tool_call("redis_read", {"command": "GET a"}), final("no redis")])
    out = agent.run("what is in redis")
    assert out["steps"][0]["status"] == "error" and out["summary"] == "no redis"


def test_fan_out_adds_database_column(config):
    agent, *_ = make_agent(config, [tool_call("fan_out_query", {"sql": "SELECT 1 AS x"}), final("done")])
    out = agent.run("where does table x live")
    [res] = out["results"]
    assert res["columns"][0] == "_database" and {r[0] for r in res["rows"]} == {"demo", "other"}


def test_exploration_results_are_hidden_unless_shown(config):
    agent, *_ = make_agent(config, [tool_call("list_tables", {"database": "demo"}),
                                    tool_call("list_tables", {"database": "demo", "show_to_user": True}, "t2"),
                                    final("done")])
    out = agent.run("list the tables")
    assert len(out["results"]) == 1 and out["results"][0]["columns"] == ["table", "approx_rows"]


def test_step_limit(config):
    config.llm["max_steps"] = 2
    agent, *_ = make_agent(config, [tool_call("list_databases", {}), tool_call("list_databases", {}, "t2")])
    out = agent.run("loop forever")
    assert "step limit" in " ".join(out["warnings"])


# ---------------------------------------------------------------- HTTP API
@pytest.fixture
def client(config):
    llm = ScriptedLLM([tool_call("run_sql", {"database": "demo", "sql": SQL}), final("ok")])
    return TestClient(create_app(config, llm=llm, hub=FakeHub(config)))


def test_api_environments_hide_connection_details(client):
    body = client.get("/api/environments").json()
    assert {e["name"] for e in body} == {"local", "staging", "sandbox", "prod", "payroll-prod"}
    assert "localhost" not in json.dumps(body) and "port" not in json.dumps(body)


def test_api_chat(client):
    r = client.post("/api/chat", json={"message": "count per department", "environment": "auto"})
    assert r.status_code == 200 and r.json()["environment"] == "local"


def test_api_chat_unknown_environment(client):
    assert client.post("/api/chat", json={"message": "x", "environment": "nope"}).status_code == 400


def test_api_query_rejects_writes_with_422(client):
    r = client.post("/api/query", json={"environment": "prod", "database": "demo", "statement": "DROP TABLE employees"})
    assert r.status_code == 422 and "rejected" in r.json()["detail"]


def test_api_query_runs_a_select(client):
    r = client.post("/api/query", json={"environment": "local", "database": "demo",
                                        "statement": "SELECT 1 FROM companies"})
    assert r.status_code == 200 and r.json()["environment"] == "local"


def test_api_serves_the_chat_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "<textarea" in r.text


def test_api_rejects_foreign_host_header(client):
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400


def test_cors_allows_local_pages_only(client):
    ok = client.options("/api/chat", headers={"origin": "http://localhost:8000", "access-control-request-method": "POST",
                                              "access-control-request-headers": "content-type"})
    bad = client.options("/api/chat", headers={"origin": "https://evil.example.com", "access-control-request-method": "POST",
                                               "access-control-request-headers": "content-type"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert "access-control-allow-origin" not in bad.headers
