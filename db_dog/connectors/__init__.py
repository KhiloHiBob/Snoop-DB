"""Connector hub: hands out one (cached) connector per (environment, store)."""

from __future__ import annotations

from typing import Any

from ..config import Config

_STORE_CLASSES = {
    "postgres": ("db_dog.connectors.postgres", "PostgresConnector"),
    "singlestore": ("db_dog.connectors.singlestore", "SingleStoreConnector"),
    "redis": ("db_dog.connectors.redis_conn", "RedisConnector"),
    "mongo": ("db_dog.connectors.mongo", "MongoConnector"),
    "opensearch": ("db_dog.connectors.opensearch", "OpenSearchConnector"),
}


class ConnectorHub:
    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[tuple[str, str], Any] = {}

    def stores_for(self, env_name: str) -> list[str]:
        return list(self.config.environments[env_name].stores)

    def get(self, env_name: str, store: str) -> Any:
        env = self.config.environments[env_name]
        if store not in env.stores:
            raise KeyError(f"environment '{env_name}' has no '{store}' store configured")
        key = (env_name, store)
        if key not in self._cache:
            import importlib

            module, cls = _STORE_CLASSES[store]
            self._cache[key] = getattr(importlib.import_module(module), cls)(self.config, env)
        return self._cache[key]
