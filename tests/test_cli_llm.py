import json
import subprocess

import pytest

from db_dog.agent import llm as llm_mod
from db_dog.agent.llm import ClaudeCLILLM, LLMUnavailable, extract_json, make_llm, render_transcript


@pytest.mark.parametrize("text,expected", [
    ('{"final": "hi"}', {"final": "hi"}),
    ('```json\n{"final": "hi"}\n```', {"final": "hi"}),
    ('Sure! {"tool_calls": [{"name": "run_sql", "input": {"sql": "SELECT \'}\'"}}]} thanks', None),
    ('no json here', None),
    ('{"a": {"b": 1}} trailing {"c": 2}', {"a": {"b": 1}}),
])
def test_extract_json(text, expected):
    got = extract_json(text)
    if expected is None and "tool_calls" in text:
        assert got["tool_calls"][0]["input"]["sql"] == "SELECT '}'"   # braces inside strings are handled
    else:
        assert got == expected


def fake_run(stdout, code=0):
    def run(cmd, **kw):
        run.cmd, run.kw = cmd, kw
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="boom")
    return run


def test_cli_final_answer(monkeypatch):
    run = fake_run(json.dumps({"result": '{"final": "There are 3."}'}))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ClaudeCLILLM, "available", staticmethod(lambda b="claude": True))
    r = ClaudeCLILLM().create("sys", [{"role": "user", "content": "q"}], [{"name": "run_sql"}])
    assert r.stop_reason == "end_turn" and r.content[0]["text"] == "There are 3."
    # Claude is locked down: no native tools, no persistence, no user settings/hooks.
    assert run.cmd[run.cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in run.cmd and "--strict-mcp-config" in run.cmd
    assert run.cmd[run.cmd.index("--setting-sources") + 1] == "project"


def test_cli_tool_calls(monkeypatch):
    out = {"result": '{"tool_calls": [{"name": "run_sql", "input": {"database": "d", "sql": "SELECT 1"}}]}'}
    monkeypatch.setattr(subprocess, "run", fake_run(json.dumps(out)))
    monkeypatch.setattr(ClaudeCLILLM, "available", staticmethod(lambda b="claude": True))
    r = ClaudeCLILLM().create("sys", [{"role": "user", "content": "q"}], [])
    assert r.stop_reason == "tool_use" and r.content[0]["name"] == "run_sql"
    assert r.content[0]["input"]["sql"] == "SELECT 1" and r.content[0]["id"].startswith("cli_")


def test_cli_plain_text_falls_back_to_final(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(json.dumps({"result": "just prose"})))
    monkeypatch.setattr(ClaudeCLILLM, "available", staticmethod(lambda b="claude": True))
    assert ClaudeCLILLM().create("s", [{"role": "user", "content": "q"}], []).content[0]["text"] == "just prose"


def test_cli_failure_is_reported(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("", code=1))
    monkeypatch.setattr(ClaudeCLILLM, "available", staticmethod(lambda b="claude": True))
    with pytest.raises(LLMUnavailable, match="logged in"):
        ClaudeCLILLM().create("s", [{"role": "user", "content": "q"}], [])


def test_transcript_includes_tool_results():
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "run_sql", "input": {"a": 1}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": '{"n": 2}'}]}]
    t = render_transcript(msgs)
    assert "[TOOL RESULT]" in t and "run_sql" in t and '{"n": 2}' in t


def test_backend_selection(monkeypatch):
    cfg = {"model": "m", "max_tokens": 10}
    monkeypatch.setattr(ClaudeCLILLM, "available", staticmethod(lambda b="claude": True))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(make_llm({**cfg, "backend": "auto"}), ClaudeCLILLM)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(make_llm({**cfg, "backend": "auto"}), llm_mod.AnthropicLLM)
    assert isinstance(make_llm({**cfg, "backend": "claude_cli"}), ClaudeCLILLM)
    assert isinstance(make_llm({**cfg, "backend": "anthropic_api"}), llm_mod.AnthropicLLM)
