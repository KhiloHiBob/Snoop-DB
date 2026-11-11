import pytest

from snoop_db.config import ROOT, load_config
from snoop_db.envs import resolve_environment
from snoop_db.safety.policy import prepare_sql


@pytest.mark.parametrize("question,expected", [
    ("how many employees are there?", "local"),
    ("how many employees in staging?", "staging"),
    ("what tables exist on stage", "staging"),
    ("count rows in prod", "prod"),
    ("check production data", "prod"),
    ("look at payroll prod for company 5", "payroll-prod"),
    ("in the sandbox, list users", "sandbox"),
    ("show me the product catalog", "local"),          # 'product' must not match 'prod'
    ("what is the locale of company 3", "local"),       # 'locale' must not match 'local' and falls back to default
])
def test_environment_from_wording(config, question, expected):
    assert resolve_environment(config, question).env == expected


def test_explicit_environment_wins(config):
    assert resolve_environment(config, "count rows in prod", "staging").env == "staging"


def test_unknown_explicit_environment(config):
    with pytest.raises(KeyError):
        resolve_environment(config, "x", "nowhere")


def test_ambiguity_is_reported(config):
    r = resolve_environment(config, "compare staging and prod")
    assert r.env == "prod" and r.ambiguous == ["staging"]


def test_example_config_loads_without_secrets():
    cfg = load_config(ROOT / "config" / "config.example.yaml")
    assert "local" in cfg.environments
    assert cfg.environments["prod-example"].production
    assert cfg.environments["prod-example"].company_id_enforced   # production implies enforcement
    assert cfg.allow_prod_rows_to_llm is False


def test_production_defaults_to_enforced(config):
    assert config.environments["prod"].company_id_enforced
    assert config.environments["payroll-prod"].company_id_enforced
    assert not config.environments["local"].company_id_enforced


def test_company_id_enforced_on_prod(config):
    r = prepare_sql("SELECT * FROM employees", "postgres", config, config.environments["prod"], {"employees"})
    assert not r.ok and "company_id" in r.reason
    ok = prepare_sql("SELECT * FROM employees WHERE company_id = 3", "postgres", config, config.environments["prod"], {"employees"})
    assert ok.ok


def test_company_id_only_warns_on_local(config):
    r = prepare_sql("SELECT * FROM employees", "postgres", config, config.environments["local"], {"employees"})
    assert r.ok and any("company_id" in w for w in r.warnings)


def test_tables_without_company_id_are_not_affected(config):
    r = prepare_sql("SELECT * FROM companies", "postgres", config, config.environments["prod"], {"employees"})
    assert r.ok and not r.warnings


def test_metadata_queries_are_exempt(config):
    r = prepare_sql("SELECT * FROM information_schema.columns WHERE table_name = 'employees'",
                    "postgres", config, config.environments["prod"], {"columns"})
    assert r.ok


def test_exempt_tables_can_be_configured(config):
    config.company_id_exempt_tables = ["employees"]
    assert prepare_sql("SELECT count(*) FROM employees", "postgres", config, config.environments["prod"], {"employees"}).ok


def test_guard_rejections_pass_through_policy(config):
    r = prepare_sql("DELETE FROM employees", "postgres", config, config.environments["local"], set())
    assert not r.ok
