from __future__ import annotations

import logging

__all__ = ["PluginRegistry", "get_registry"]

_log = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._source: dict[str, type] = {}
        self._destination: dict[str, type] = {}
        self._meta: list[dict] = []

    def load(self) -> None:
        from importlib.metadata import entry_points

        _dist_info: dict[str, dict] = {}

        def _record(ep, direction: str) -> None:
            dist = getattr(ep, "dist", None)
            key = dist.metadata["Name"] if dist else ep.value.split(".")[0]
            if key not in _dist_info:
                _dist_info[key] = {
                    "dist": dist,
                    "source_protocols": [],
                    "destination_protocols": [],
                }
            _dist_info[key][direction].append(ep.name)

        for ep in entry_points(group="groupware_migrator.source_connectors"):
            try:
                cls = ep.load()
                self._source[ep.name] = cls
                _record(ep, "source_protocols")
            except Exception as exc:
                _log.warning("Failed to load source connector plugin %r: %s", ep.name, exc)

        for ep in entry_points(group="groupware_migrator.destination_connectors"):
            try:
                cls = ep.load()
                self._destination[ep.name] = cls
                _record(ep, "destination_protocols")
            except Exception as exc:
                _log.warning("Failed to load destination connector plugin %r: %s", ep.name, exc)

        for key, info in _dist_info.items():
            dist = info["dist"]
            self._meta.append({
                "name": dist.metadata["Name"] if dist else key,
                "version": dist.metadata["Version"] if dist else "unknown",
                "source_protocols": info["source_protocols"],
                "destination_protocols": info["destination_protocols"],
            })

    def get_source(self, protocol: str) -> type | None:
        return self._source.get(protocol)

    def get_destination(self, protocol: str) -> type | None:
        return self._destination.get(protocol)

    def list_plugins(self) -> list[dict]:
        return list(self._meta)


_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.load()
    return _registry
