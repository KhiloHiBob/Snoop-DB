from __future__ import annotations

from fastapi.testclient import TestClient

from db_dog.app import create_app
from db_dog.threads import ThreadStoreUnavailable, strip_production_rows
from .conftest import FakeHub, ScriptedLLM


class MemStore:
    def __init__(self):
        self.data = {}

    def list(self):
        return [{"id": k, "title": v[0], "turns": v[1], "updated_at": "now"} for k, v in self.data.items()]

    def save(self, tid, title, turns):
        self.data[tid] = (title, strip_production_rows(turns))

    def delete(self, tid):
        self.data.pop(tid, None)


class DownStore(MemStore):
    def list(self):
        raise ThreadStoreUnavailable("store down")


def client(config, store):
    return TestClient(create_app(config, ScriptedLLM([]), FakeHub(config), store))


def test_production_rows_are_not_stored():
    turns = [{"text": "q", "answer": {"production": True, "results": [{"rows": [[1]]}], "summary": "s"}},
             {"text": "q2", "answer": {"production": False, "results": [{"rows": [[2]]}]}}]
    out = strip_production_rows(turns)
    assert out[0]["answer"]["results"] == [] and out[0]["answer"]["summary"] == "s"
    assert out[0]["answer"]["warnings"]
    assert out[1]["answer"]["results"] == [{"rows": [[2]]}]


def test_thread_roundtrip(config):
    c = client(config, MemStore())
    assert c.put("/api/threads/t1", json={"title": "Hello", "turns": [{"text": "hi", "kind": "answer"}]}).status_code == 200
    got = c.get("/api/threads").json()
    assert got[0]["id"] == "t1" and got[0]["turns"][0]["text"] == "hi"
    assert c.delete("/api/threads/t1").status_code == 200
    assert c.get("/api/threads").json() == []


def test_bad_id_rejected_and_store_down_is_503(config):
    c = client(config, MemStore())
    assert c.delete("/api/threads/bad id!").status_code in (400, 404)
    assert client(config, DownStore()).get("/api/threads").status_code == 503
