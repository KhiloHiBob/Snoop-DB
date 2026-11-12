import pytest

from db_dog.config import ROOT, load_config
from db_dog.envs import resolve_environment
from db_dog.safety.policy import prepare_sql


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


# ---------------------------------------------------------------- big tables
BIG = {"audit_log": 5_000_000}


def test_big_table_needs_company_id_in_every_environment(config):
    for env in ("local", "staging", "sandbox", "prod"):
        r = prepare_sql("SELECT id FROM audit_log ORDER BY id DESC", "postgres", config,
                        config.environments[env], {"audit_log"}, BIG)
        assert not r.ok, env
        assert "ask the user" in r.reason and "5,000,000" in r.reason


def test_big_table_with_company_id_is_allowed(config):
    r = prepare_sql("SELECT id FROM audit_log WHERE company_id = 7 ORDER BY id DESC", "postgres", config,
                    config.environments["local"], {"audit_log"}, BIG)
    assert r.ok


def test_counting_a_big_table_blind_is_rejected(config):
    r = prepare_sql("SELECT count(*) FROM audit_log", "postgres", config, config.environments["local"], {"audit_log"}, BIG)
    assert not r.ok


def test_small_tenant_table_still_only_warns_on_local(config):
    r = prepare_sql("SELECT id FROM employees", "postgres", config, config.environments["local"],
                    {"employees", "audit_log"}, BIG)
    assert r.ok and r.warnings


def test_big_table_metadata_queries_are_exempt(config):
    r = prepare_sql("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log'",
                    "postgres", config, config.environments["local"], {"audit_log"}, BIG)
    assert r.ok


def test_big_table_join_still_requires_the_filter(config):
    r = prepare_sql("SELECT e.id FROM employees e JOIN audit_log a ON a.employee_id = e.id", "postgres", config,
                    config.environments["local"], {"employees", "audit_log"}, BIG)
    assert not r.ok


def test_exempt_tables_bypass_the_big_table_rule(config):
    config.company_id_exempt_tables = ["audit_log"]
    assert prepare_sql("SELECT id FROM audit_log", "postgres", config, config.environments["local"],
                       {"audit_log"}, BIG).ok
