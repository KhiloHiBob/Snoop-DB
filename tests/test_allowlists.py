import pytest

from snoop_db.connectors.base import shape_rows, to_jsonable
from snoop_db.connectors.mongo import validate_filter, validate_pipeline
from snoop_db.connectors.opensearch import validate_request
from snoop_db.connectors.postgres import PolicyError
from snoop_db.connectors.redis_conn import validate_command


# ---------------------------------------------------------------- redis
@pytest.mark.parametrize("cmd", [
    "GET session:1", "HGETALL user:5", "TYPE k", "TTL k", "LRANGE q 0 49", "SMEMBERS s",
    "SCAN 0 MATCH user:* COUNT 100", "INFO keyspace", "MEMORY USAGE k", "DBSIZE", "ZRANGE z 0 10 WITHSCORES",
])
def test_redis_reads_allowed(cmd):
    assert validate_command(cmd)[0] == cmd.split()[0].upper()


@pytest.mark.parametrize("cmd", [
    "SET a 1", "DEL a", "FLUSHALL", "FLUSHDB", "EXPIRE a 1", "CONFIG SET dir /tmp", "EVAL 'return 1' 0",
    "SHUTDOWN", "SLAVEOF host 1", "MIGRATE h 1 k 0 1", "SCRIPT FLUSH", "MEMORY PURGE", "OBJECT FREQ",
    "DEBUG SLEEP 5", "PUBLISH c m", "", "   ",
])
def test_redis_writes_and_admin_rejected(cmd):
    if cmd == "OBJECT FREQ":
        validate_command(cmd)  # read-only sub-command, allowed
        return
    with pytest.raises(PolicyError):
        validate_command(cmd)


def test_redis_keys_points_to_scan():
    with pytest.raises(PolicyError, match="SCAN"):
        validate_command("KEYS *")


def test_redis_scan_count_is_capped_or_added():
    assert validate_command("SCAN 0 COUNT 999999")[-1] == "1000"
    assert validate_command("SCAN 0")[-2:] == ["COUNT", "100"]


def test_redis_quoted_arguments():
    assert validate_command('GET "key with spaces"') == ["GET", "key with spaces"]
    with pytest.raises(PolicyError):
        validate_command('GET "unterminated')


# ---------------------------------------------------------------- mongo
def test_mongo_filter_rejects_javascript_operators():
    assert validate_filter({"company_id": 1, "status": {"$in": ["a"]}})
    with pytest.raises(PolicyError):
        validate_filter({"$where": "sleep(1000)"})
    with pytest.raises(PolicyError):
        validate_filter({"a": {"$expr": {"$function": {"body": "x", "args": [], "lang": "js"}}}})
    with pytest.raises(PolicyError):
        validate_filter(["not", "an", "object"])


def test_mongo_pipeline_stage_allowlist():
    assert validate_pipeline([{"$match": {"a": 1}}, {"$group": {"_id": "$a", "n": {"$sum": 1}}}])
    for bad in ({"$out": "x"}, {"$merge": {"into": "x"}}, {"$currentOp": {}}, {"$planCacheStats": {}}):
        with pytest.raises(PolicyError):
            validate_pipeline([{"$match": {}}, bad])
    with pytest.raises(PolicyError):
        validate_pipeline([{"$match": {}, "$limit": 1}])   # two operators in one stage
    with pytest.raises(PolicyError):
        validate_pipeline([])
    with pytest.raises(PolicyError):
        validate_pipeline([{"$lookup": {"from": "x", "pipeline": [{"$out": "y"}]}}])  # nested


# ---------------------------------------------------------------- opensearch
@pytest.mark.parametrize("method,path", [
    ("GET", "/_cat/indices"), ("GET", "/_cluster/health"), ("GET", "/logs-*/_mapping"),
    ("POST", "/logs-2025/_search"), ("POST", "/logs/_count"), ("GET", "/logs/_doc/abc123"),
])
def test_opensearch_reads_allowed(method, path):
    assert validate_request(method, path, {"query": {"match_all": {}}} if method == "POST" else None)


@pytest.mark.parametrize("method,path", [
    ("DELETE", "/logs"), ("PUT", "/logs"), ("POST", "/logs/_doc"), ("POST", "/logs/_delete_by_query"),
    ("POST", "/logs/_update_by_query"), ("POST", "/_bulk"), ("GET", "/_nodes"), ("POST", "/_reindex"),
    ("PUT", "/_cluster/settings"), ("POST", "/logs/_close"),
])
def test_opensearch_writes_rejected(method, path):
    with pytest.raises(PolicyError):
        validate_request(method, path, {})


def test_opensearch_scripts_and_size():
    with pytest.raises(PolicyError):
        validate_request("POST", "/i/_search", {"query": {"script": {"script": "1"}}})
    assert validate_request("POST", "/i/_search", {"size": 100000})[2]["size"] == 200


# ---------------------------------------------------------------- result shaping
def test_to_jsonable_handles_db_types():
    import datetime as dt, decimal, uuid
    assert to_jsonable(decimal.Decimal("1.50")) == "1.50"
    assert to_jsonable(dt.date(2025, 1, 8)) == "2025-01-08"
    assert to_jsonable(dt.time(12, 52, 35, 963245)) == "12:52:35.963245"
    assert to_jsonable(uuid.UUID(int=1)).endswith("0001")
    assert to_jsonable(b"abc").startswith("<3 bytes")
    assert to_jsonable(float("nan")) == "nan"
    assert to_jsonable("x" * 50, max_cell_chars=10).endswith("[50 chars]")
    assert "chars]" in to_jsonable({"k": "v" * 500}, max_cell_chars=50)


def test_shape_rows_respects_byte_budget():
    rows, truncated = shape_rows([("a" * 100,)] * 50, 1000, 1000)
    assert truncated and 0 < len(rows) < 50
    rows, truncated = shape_rows([(1,), (2,)], 1000, 1000)
    assert rows == [[1], [2]] and not truncated
