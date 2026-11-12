from __future__ import annotations

import pytest

from db_dog.agent import modules


@pytest.fixture
def mods(tmp_path, monkeypatch):
    (tmp_path / "timeoff.md").write_text("# timeoff skill: timeoff_requests")
    (tmp_path / "employee.md").write_text("# employee skill")
    monkeypatch.setattr(modules, "MODULES_DIR", tmp_path)
    monkeypatch.setattr(modules, "MODELS_DIR", tmp_path / "no-models")
    return tmp_path


def test_select_by_name_and_alias(mods):
    assert modules.select("show me the pto balance") == ["timeoff"]
    assert modules.select("list employees") == ["employee"]
    assert modules.select("something unrelated") == []


def test_weak_second_hit_not_preloaded(mods):
    assert modules.select("timeoff request events for employee 1") == ["timeoff"]


def test_load_and_unknown(mods):
    assert "timeoff_requests" in modules.load("timeoff")
    with pytest.raises(KeyError):
        modules.load("nope")


def test_index_empty_without_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(modules, "MODULES_DIR", tmp_path / "missing")
    monkeypatch.setattr(modules, "MODELS_DIR", tmp_path / "missing2")
    assert modules.index_text() == "" and modules.available() == []


def test_models_dir_is_merged(mods, monkeypatch):
    models = mods / "models"
    (models / "payroll").mkdir(parents=True)
    (models / "payroll" / "payroll.md").write_text("# payroll tables: payroll_runs")
    monkeypatch.setattr(modules, "MODELS_DIR", models)
    assert "payroll" in modules.available()
    assert "payroll_runs" in modules.load("payroll")
    assert "timeoff_requests" in modules.load("timeoff")   # modules/ doc still loads without a models entry
