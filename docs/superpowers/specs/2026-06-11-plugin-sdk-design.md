# Plugin / Connector SDK Design

**Date:** 2026-06-11  
**Status:** Approved

## Goal

Allow third-party Python packages to add new source/destination connectors (e.g. EWS, Google Workspace API, Nextcloud) without modifying the core codebase. A plugin installed via `pip` is automatically discovered and usable in migration configs.

---

## Architecture

Four pieces: a public SDK module, a plugin registry, small updates to the factory and domain model, one new admin endpoint, and an example plugin.

```
groupware_migrator/
  sdk/__init__.py          (new)  — public API for plugin authors
  engine/plugin_registry.py (new) — entry-point discovery, protocol → class map
  connectors/factory.py   (mod)  — fallback to registry after built-in dispatch
  models/domain.py        (mod)  — from_dict falls back to raw string for unknown protocols;
                                    _validate_workload_protocols skips for plugin protocols
  api/routers/admin_router.py (mod) — GET /admin/plugins
examples/
  sample_plugin/           (new)  — minimal working example package
tests/
  test_plugin_sdk.py       (new)  — all tests
```

---

## `groupware_migrator.sdk`

The public surface that plugin authors import from. Re-exports only what they need — no internal modules exposed.

```python
from groupware_migrator.connectors.base import SourceConnector, DestinationConnector
from groupware_migrator.models.domain import (
    ConnectionConfig,
    SourceItem,
    SourceMessage,
    CollectionSnapshot,
    MailboxSnapshot,
)

__all__ = [
    "SourceConnector",
    "DestinationConnector",
    "ConnectionConfig",
    "SourceItem",
    "SourceMessage",
    "CollectionSnapshot",
    "MailboxSnapshot",
]
```

Plugin authors install `groupware-migrator` as a dependency and import from `groupware_migrator.sdk`. The SDK version is the package version — no separate SDK versioning.

---

## Plugin Registration (Entry Points)

Plugin packages declare connectors in their `pyproject.toml`:

```toml
[project.entry-points."groupware_migrator.source_connectors"]
ews = "my_package.connector:EWSSourceConnector"

[project.entry-points."groupware_migrator.destination_connectors"]
ews = "my_package.connector:EWSDestinationConnector"
```

The key (`ews`) becomes the protocol string used in migration configs. After `pip install my-ews-connector`, a config with `"protocol": "ews"` works automatically.

Entry-point groups:
- `groupware_migrator.source_connectors`
- `groupware_migrator.destination_connectors`

---

## `engine/plugin_registry.py`

Module-level singleton, lazy-loaded on first use (no startup cost when no plugins installed).

```python
class PluginRegistry:
    def __init__(self) -> None:
        self._source: dict[str, type[SourceConnector]] = {}
        self._destination: dict[str, type[DestinationConnector]] = {}
        self._meta: list[dict] = []   # for /admin/plugins

    def load(self) -> None:
        """Discover and load all installed connector plugins."""
        from importlib.metadata import entry_points, packages_distributions
        ...

    def get_source(self, protocol: str) -> type[SourceConnector] | None: ...
    def get_destination(self, protocol: str) -> type[DestinationConnector] | None: ...
    def list_plugins(self) -> list[dict]: ...


_registry: PluginRegistry | None = None

def get_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.load()
    return _registry
```

**`list_plugins()` response shape** (one entry per installed plugin package):
```python
[
    {
        "name": "my-ews-connector",
        "version": "1.0.0",
        "source_protocols": ["ews"],
        "destination_protocols": ["ews"],
    }
]
```

**Error handling during load:** if an entry point fails to import, log a warning and skip it. A broken plugin must not prevent the application from starting.

---

## `connectors/factory.py` Changes

After the built-in enum dispatch, fall back to the registry:

```python
def create_source_connector(request: MigrationRequest) -> SourceConnector:
    # ... existing if/elif chain ...
    # fallback:
    cls = get_registry().get_source(str(request.source.protocol))
    if cls is not None:
        return cls(request.source.connection)
    raise ValueError(f"Unsupported source protocol: {request.source.protocol}")
```

Same pattern for `create_destination_connector`. The existing dispatch is unchanged — plugin lookup only happens for protocols not matching any built-in.

---

## `models/domain.py` Changes

### `SourceEndpoint.from_dict` / `DestinationEndpoint.from_dict`

Currently raises `ValueError` for unknown protocols at parse time. Change to fall back to raw string:

```python
@classmethod
def from_dict(cls, payload: dict) -> "SourceEndpoint":
    raw = str(payload["protocol"]).lower()
    try:
        protocol: SourceProtocol | str = SourceProtocol(raw)
    except ValueError:
        protocol = raw   # plugin protocol — validated at factory time
    # default_port: 443 for unknown protocols
    ...
```

### `to_dict`

`self.protocol.value` → `str(self.protocol)` — works for both StrEnum and plain string.

### `_validate_workload_protocols`

Skip built-in workload/protocol compatibility checks when either protocol is not a known built-in value:

```python
def _validate_workload_protocols(workload, source_protocol, destination_protocol):
    if source_protocol not in set(SourceProtocol) or destination_protocol not in set(DestinationProtocol):
        return  # plugin protocols define their own compatibility
    # ... existing checks unchanged ...
```

---

## `GET /admin/plugins` Endpoint

Admin-only. Returns the list from `get_registry().list_plugins()`.

```json
[
  {
    "name": "my-ews-connector",
    "version": "1.0.0",
    "source_protocols": ["ews"],
    "destination_protocols": ["ews"]
  }
]
```

Empty list `[]` when no plugins installed.

---

## Example Plugin (`examples/sample_plugin/`)

A minimal but complete example that can be installed with `pip install -e examples/sample_plugin/`:

```
examples/sample_plugin/
  pyproject.toml          — entry points, depends on groupware-migrator
  src/sample_plugin/
    __init__.py
    connector.py          — SampleSourceConnector + SampleDestinationConnector
  README.md               — 20-line guide for plugin authors
```

The sample connector implements `validate()`, `list_mailboxes()`, and `iter_messages()` with stub no-op bodies, showing the minimum required surface.

---

## Testing

All tests in `tests/test_plugin_sdk.py`. No real entry points needed — registry is injected directly for unit tests.

### `TestSDKPublicAPI`
- `test_sdk_exports_source_connector`
- `test_sdk_exports_destination_connector`
- `test_sdk_exports_connection_config`

### `TestPluginRegistry`
- `test_empty_registry_returns_none_for_unknown_protocol`
- `test_registered_source_connector_is_returned`
- `test_registered_destination_connector_is_returned`
- `test_list_plugins_returns_empty_when_no_plugins`
- `test_list_plugins_returns_metadata`

### `TestFactoryWithPlugin`
- `test_factory_uses_registered_source_plugin`
- `test_factory_uses_registered_destination_plugin`
- `test_factory_raises_for_truly_unknown_protocol`

### `TestDomainModelChanges`
- `test_source_endpoint_parses_builtin_protocol`
- `test_source_endpoint_accepts_plugin_protocol_string`
- `test_destination_endpoint_accepts_plugin_protocol_string`
- `test_workload_validation_skipped_for_plugin_protocols`
- `test_to_dict_works_for_plugin_protocol`

### `TestAdminPluginsEndpoint`
- `test_returns_empty_list_when_no_plugins`
- `test_returns_plugin_metadata`
- `test_requires_admin`

---

## Constraints

- Plugin protocols must not collide with built-in protocol names (`imap`, `pop3`, `caldav`, `carddav`)
- Plugin connector `__init__` receives `ConnectionConfig` as its sole argument
- No plugin can override a built-in connector (registry is only checked after built-in dispatch fails)
- `get_registry()` is process-global; reloading plugins requires process restart
