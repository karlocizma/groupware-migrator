# Plugin / Connector SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow third-party Python packages to register new source/destination connectors via setuptools entry points, usable in migration configs without modifying core code.

**Architecture:** A new `engine/plugin_registry.py` singleton discovers `groupware_migrator.source_connectors` / `groupware_migrator.destination_connectors` entry points on first use. `connectors/factory.py` falls back to the registry after built-in enum dispatch. `models/domain.py` accepts unknown protocol strings instead of raising at parse time. `sdk/__init__.py` re-exports the public surface plugin authors depend on.

**Tech Stack:** Python `importlib.metadata` (stdlib), `setuptools` entry points, FastAPI, `unittest.mock`.

---

## File Map

| File | Change |
|---|---|
| `src/groupware_migrator/engine/plugin_registry.py` | **Create** — `PluginRegistry`, `get_registry()` |
| `src/groupware_migrator/sdk/__init__.py` | **Create** — public API re-exports |
| `src/groupware_migrator/models/domain.py` | **Modify** — `from_dict` fallback, `to_dict` fix, `_validate_workload_protocols` skip |
| `src/groupware_migrator/connectors/factory.py` | **Modify** — registry fallback after built-in dispatch |
| `src/groupware_migrator/api/routers/admin_router.py` | **Modify** — add `GET /admin/plugins` |
| `examples/sample_plugin/` | **Create** — minimal working example package |
| `tests/test_plugin_sdk.py` | **Create** — all tests for this feature |

---

### Task 1: `engine/plugin_registry.py` — PluginRegistry

**Files:**
- Create: `src/groupware_migrator/engine/plugin_registry.py`
- Create: `tests/test_plugin_sdk.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_plugin_sdk.py`:

```python
"""Tests for Plugin/Connector SDK."""
from __future__ import annotations

import unittest
from typing import Iterable

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.engine.plugin_registry import PluginRegistry, get_registry
from groupware_migrator.models.domain import (
    ConnectionConfig,
    MailboxSnapshot,
    SourceMessage,
)


# ---------------------------------------------------------------------------
# Fake connectors used across multiple test classes
# ---------------------------------------------------------------------------

class _FakeSourceConnector(SourceConnector):
    protocol = "fake_proto"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        return []

    def iter_messages(self, mailbox: str, resume_from: str | None = None) -> Iterable[SourceMessage]:
        return iter([])


class _FakeDestinationConnector(DestinationConnector):
    protocol = "fake_proto"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def ensure_mailbox(self, mailbox: str) -> None:
        pass

    def append_message(self, mailbox: str, raw_message: bytes, *, flags=None, internal_date=None) -> str | None:
        return None


# ---------------------------------------------------------------------------
# TestPluginRegistry
# ---------------------------------------------------------------------------

class TestPluginRegistry(unittest.TestCase):
    def test_empty_registry_returns_none_for_unknown_source(self):
        reg = PluginRegistry()
        self.assertIsNone(reg.get_source("no_such_proto"))

    def test_empty_registry_returns_none_for_unknown_destination(self):
        reg = PluginRegistry()
        self.assertIsNone(reg.get_destination("no_such_proto"))

    def test_registered_source_connector_is_returned(self):
        reg = PluginRegistry()
        reg._source["fake_proto"] = _FakeSourceConnector
        self.assertIs(reg.get_source("fake_proto"), _FakeSourceConnector)

    def test_registered_destination_connector_is_returned(self):
        reg = PluginRegistry()
        reg._destination["fake_proto"] = _FakeDestinationConnector
        self.assertIs(reg.get_destination("fake_proto"), _FakeDestinationConnector)

    def test_list_plugins_returns_empty_when_no_plugins(self):
        reg = PluginRegistry()
        self.assertEqual(reg.list_plugins(), [])

    def test_list_plugins_returns_metadata(self):
        reg = PluginRegistry()
        reg._meta = [{
            "name": "my-plugin",
            "version": "1.0.0",
            "source_protocols": ["fake_proto"],
            "destination_protocols": [],
        }]
        plugins = reg.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "my-plugin")
        self.assertEqual(plugins[0]["source_protocols"], ["fake_proto"])

    def test_get_registry_returns_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        self.assertIs(r1, r2)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestPluginRegistry -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'groupware_migrator.engine.plugin_registry'`

- [ ] **Step 3: Create `src/groupware_migrator/engine/plugin_registry.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestPluginRegistry -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/engine/plugin_registry.py tests/test_plugin_sdk.py
git commit -m "feat: add PluginRegistry for connector plugin discovery"
```

---

### Task 2: `sdk/__init__.py` — Public SDK API

**Files:**
- Create: `src/groupware_migrator/sdk/__init__.py`
- Modify: `tests/test_plugin_sdk.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_plugin_sdk.py`:

```python
# ---------------------------------------------------------------------------
# TestSDKPublicAPI
# ---------------------------------------------------------------------------

class TestSDKPublicAPI(unittest.TestCase):
    def test_sdk_exports_source_connector(self):
        from groupware_migrator.sdk import SourceConnector as SDKSource
        from groupware_migrator.connectors.base import SourceConnector as BaseSource
        self.assertIs(SDKSource, BaseSource)

    def test_sdk_exports_destination_connector(self):
        from groupware_migrator.sdk import DestinationConnector as SDKDest
        from groupware_migrator.connectors.base import DestinationConnector as BaseDest
        self.assertIs(SDKDest, BaseDest)

    def test_sdk_exports_connection_config(self):
        from groupware_migrator.sdk import ConnectionConfig as SDKConfig
        from groupware_migrator.models.domain import ConnectionConfig as DomainConfig
        self.assertIs(SDKConfig, DomainConfig)

    def test_sdk_exports_source_item(self):
        from groupware_migrator.sdk import SourceItem
        self.assertIsNotNone(SourceItem)

    def test_sdk_exports_source_message(self):
        from groupware_migrator.sdk import SourceMessage
        self.assertIsNotNone(SourceMessage)

    def test_sdk_exports_collection_snapshot(self):
        from groupware_migrator.sdk import CollectionSnapshot
        self.assertIsNotNone(CollectionSnapshot)

    def test_sdk_exports_mailbox_snapshot(self):
        from groupware_migrator.sdk import MailboxSnapshot
        self.assertIsNotNone(MailboxSnapshot)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestSDKPublicAPI -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'groupware_migrator.sdk'`

- [ ] **Step 3: Create `src/groupware_migrator/sdk/__init__.py`**

First check what `groupware_migrator.models` exports:

```bash
grep -n "SourceItem\|SourceMessage\|CollectionSnapshot\|MailboxSnapshot" /home/karlo/projects/groupware-migrator/src/groupware_migrator/models/__init__.py
```

Then create the file (adjust imports if the grep shows different module paths):

```python
from __future__ import annotations

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.models.domain import (
    CollectionSnapshot,
    ConnectionConfig,
    MailboxSnapshot,
    SourceItem,
    SourceMessage,
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestSDKPublicAPI -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/sdk/__init__.py tests/test_plugin_sdk.py
git commit -m "feat: add groupware_migrator.sdk public API for plugin authors"
```

---

### Task 3: `models/domain.py` — Protocol string fallback

**Files:**
- Modify: `src/groupware_migrator/models/domain.py`
- Modify: `tests/test_plugin_sdk.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_plugin_sdk.py`:

```python
from groupware_migrator.models.domain import (
    DestinationEndpoint,
    DestinationProtocol,
    MigrationOptions,
    MigrationRequest,
    SourceEndpoint,
    SourceProtocol,
    WorkloadType,
)


# ---------------------------------------------------------------------------
# TestDomainModelChanges
# ---------------------------------------------------------------------------

def _src_payload(protocol: str) -> dict:
    return {
        "protocol": protocol,
        "connection": {"host": "h.example.com", "port": 443, "username": "u", "password": "p"},
    }


def _dst_payload(protocol: str) -> dict:
    return {
        "protocol": protocol,
        "connection": {"host": "h.example.com", "port": 443, "username": "u", "password": "p"},
    }


class TestDomainModelChanges(unittest.TestCase):
    def test_source_endpoint_parses_builtin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("imap"))
        self.assertIs(ep.protocol, SourceProtocol.IMAP)

    def test_source_endpoint_accepts_plugin_protocol_string(self):
        ep = SourceEndpoint.from_dict(_src_payload("ews"))
        self.assertEqual(ep.protocol, "ews")
        self.assertNotIsInstance(ep.protocol, SourceProtocol)

    def test_destination_endpoint_accepts_plugin_protocol_string(self):
        ep = DestinationEndpoint.from_dict(_dst_payload("graph"))
        self.assertEqual(ep.protocol, "graph")
        self.assertNotIsInstance(ep.protocol, DestinationProtocol)

    def test_to_dict_works_for_builtin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("imap"))
        d = ep.to_dict()
        self.assertEqual(d["protocol"], "imap")

    def test_to_dict_works_for_plugin_protocol(self):
        ep = SourceEndpoint.from_dict(_src_payload("ews"))
        d = ep.to_dict()
        self.assertEqual(d["protocol"], "ews")

    def test_workload_validation_skipped_for_plugin_source_protocol(self):
        # Should not raise — plugin protocols bypass built-in validation
        req = MigrationRequest.from_dict({
            "source": _src_payload("ews"),
            "destination": _dst_payload("ews"),
            "workload": "mail",
        })
        self.assertEqual(req.workload, WorkloadType.MAIL)

    def test_workload_validation_still_runs_for_builtin_protocols(self):
        # caldav source + imap destination for mail workload should still raise
        with self.assertRaises(ValueError):
            MigrationRequest.from_dict({
                "source": _src_payload("caldav"),
                "destination": _dst_payload("imap"),
                "workload": "mail",
            })
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestDomainModelChanges -v 2>&1 | head -15
```

Expected: `test_source_endpoint_accepts_plugin_protocol_string` fails with `ValueError: 'ews' is not a valid SourceProtocol`.

- [ ] **Step 3: Update `SourceEndpoint` in `domain.py`**

In `src/groupware_migrator/models/domain.py`:

**A) Change type annotation** (line 200) from:
```python
    protocol: SourceProtocol
```
to:
```python
    protocol: SourceProtocol | str
```

**B) Replace `from_dict` method** (lines 213–239). Replace the entire `from_dict` classmethod body:

Old:
```python
    @classmethod
    def from_dict(cls, payload: dict) -> "SourceEndpoint":
        protocol = SourceProtocol(str(payload["protocol"]).lower())
        if protocol is SourceProtocol.IMAP:
            default_port = 993
        elif protocol is SourceProtocol.POP3:
            default_port = 995
        else:
            default_port = 443
```

New:
```python
    @classmethod
    def from_dict(cls, payload: dict) -> "SourceEndpoint":
        raw = str(payload["protocol"]).lower()
        try:
            protocol: SourceProtocol | str = SourceProtocol(raw)
        except ValueError:
            protocol = raw
        if protocol is SourceProtocol.IMAP:
            default_port = 993
        elif protocol is SourceProtocol.POP3:
            default_port = 995
        else:
            default_port = 443
```

**C) Fix `to_dict`** (line 243). Change:
```python
            "protocol": self.protocol.value,
```
to:
```python
            "protocol": str(self.protocol),
```

- [ ] **Step 4: Update `DestinationEndpoint` in `domain.py`**

**A) Change type annotation** (line 253) from:
```python
    protocol: DestinationProtocol
```
to:
```python
    protocol: DestinationProtocol | str
```

**B) Replace `from_dict` first two lines** (lines 267–269). Change:

Old:
```python
    @classmethod
    def from_dict(cls, payload: dict) -> "DestinationEndpoint":
        protocol = DestinationProtocol(str(payload["protocol"]).lower())
        default_port = 993 if protocol is DestinationProtocol.IMAP else 443
```

New:
```python
    @classmethod
    def from_dict(cls, payload: dict) -> "DestinationEndpoint":
        raw = str(payload["protocol"]).lower()
        try:
            protocol: DestinationProtocol | str = DestinationProtocol(raw)
        except ValueError:
            protocol = raw
        default_port = 993 if protocol is DestinationProtocol.IMAP else 443
```

**C) Fix `to_dict`** (line 285). Change:
```python
            "protocol": self.protocol.value,
```
to:
```python
            "protocol": str(self.protocol),
```

- [ ] **Step 5: Update `_validate_workload_protocols` in `domain.py`**

Find `_validate_workload_protocols` (line 158). Add two lines at the very top of the function body, before the first `if workload is WorkloadType.MAIL:`:

```python
    if source_protocol not in set(SourceProtocol) or destination_protocol not in set(DestinationProtocol):
        return
```

The complete updated function start becomes:
```python
def _validate_workload_protocols(
    *,
    workload: WorkloadType,
    source_protocol: SourceProtocol,
    destination_protocol: DestinationProtocol,
) -> None:
    if source_protocol not in set(SourceProtocol) or destination_protocol not in set(DestinationProtocol):
        return
    if workload is WorkloadType.MAIL:
        ...
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestDomainModelChanges -v
```

Expected: 7 tests pass.

- [ ] **Step 7: Run existing model/runner tests for regressions**

```bash
PYTHONPATH=src python3 -m unittest tests/test_runner.py tests/test_ldap_auth.py -v 2>&1 | tail -5
```

Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add src/groupware_migrator/models/domain.py tests/test_plugin_sdk.py
git commit -m "feat: allow plugin protocol strings in SourceEndpoint and DestinationEndpoint"
```

---

### Task 4: `connectors/factory.py` — Registry fallback

**Files:**
- Modify: `src/groupware_migrator/connectors/factory.py`
- Modify: `tests/test_plugin_sdk.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_plugin_sdk.py`:

```python
from unittest.mock import patch

from groupware_migrator.connectors.factory import (
    create_destination_connector,
    create_source_connector,
)


# ---------------------------------------------------------------------------
# TestFactoryWithPlugin
# ---------------------------------------------------------------------------

def _make_conn() -> ConnectionConfig:
    return ConnectionConfig(host="h.example.com", port=443, username="u", password="p")


def _make_source_ep(protocol: str) -> SourceEndpoint:
    return SourceEndpoint(protocol=protocol, connection=_make_conn())


def _make_dest_ep(protocol: str) -> DestinationEndpoint:
    return DestinationEndpoint(protocol=protocol, connection=_make_conn())


def _make_request(src_proto: str, dst_proto: str) -> MigrationRequest:
    return MigrationRequest(
        source=_make_source_ep(src_proto),
        destination=_make_dest_ep(dst_proto),
        workload=WorkloadType.MAIL,
        options=MigrationOptions(),
    )


class TestFactoryWithPlugin(unittest.TestCase):
    def _reg_with_source(self) -> PluginRegistry:
        reg = PluginRegistry()
        reg._source["fake_proto"] = _FakeSourceConnector
        return reg

    def _reg_with_destination(self) -> PluginRegistry:
        reg = PluginRegistry()
        reg._destination["fake_proto"] = _FakeDestinationConnector
        return reg

    def test_factory_uses_registered_source_plugin(self):
        req = _make_request("fake_proto", "imap")
        reg = self._reg_with_source()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            connector = create_source_connector(req)
        self.assertIsInstance(connector, _FakeSourceConnector)

    def test_factory_uses_registered_destination_plugin(self):
        req = _make_request("imap", "fake_proto")
        reg = self._reg_with_destination()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            connector = create_destination_connector(req)
        self.assertIsInstance(connector, _FakeDestinationConnector)

    def test_factory_raises_for_truly_unknown_source_protocol(self):
        req = _make_request("no_such_proto", "imap")
        reg = PluginRegistry()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            with self.assertRaises(ValueError):
                create_source_connector(req)

    def test_factory_raises_for_truly_unknown_destination_protocol(self):
        req = _make_request("imap", "no_such_proto")
        reg = PluginRegistry()
        with patch("groupware_migrator.connectors.factory.get_registry", return_value=reg):
            with self.assertRaises(ValueError):
                create_destination_connector(req)

    def test_builtin_source_connectors_unaffected(self):
        req = _make_request("imap", "imap")
        from groupware_migrator.connectors.imap import ImapSourceConnector
        connector = create_source_connector(req)
        self.assertIsInstance(connector, ImapSourceConnector)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestFactoryWithPlugin -v 2>&1 | head -15
```

Expected: `test_factory_uses_registered_source_plugin` fails — `ValueError: Unsupported source protocol: fake_proto`.

- [ ] **Step 3: Update `connectors/factory.py`**

Read the current file, then replace its entire content with:

```python
from __future__ import annotations

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.connectors.dav import (
    CalDavDestinationConnector,
    CalDavSourceConnector,
    CardDavDestinationConnector,
    CardDavSourceConnector,
)
from groupware_migrator.connectors.imap import ImapDestinationConnector, ImapSourceConnector
from groupware_migrator.connectors.pop3 import Pop3SourceConnector
from groupware_migrator.engine.plugin_registry import get_registry
from groupware_migrator.models import DestinationProtocol, MigrationRequest, SourceProtocol


def create_source_connector(request: MigrationRequest) -> SourceConnector:
    if request.source.protocol is SourceProtocol.IMAP:
        return ImapSourceConnector(request.source.connection)
    if request.source.protocol is SourceProtocol.POP3:
        return Pop3SourceConnector(request.source.connection)
    if request.source.protocol is SourceProtocol.CALDAV:
        return CalDavSourceConnector(request.source.connection)
    if request.source.protocol is SourceProtocol.CARDDAV:
        return CardDavSourceConnector(request.source.connection)
    cls = get_registry().get_source(str(request.source.protocol))
    if cls is not None:
        return cls(request.source.connection)
    raise ValueError(f"Unsupported source protocol: {request.source.protocol}")


def create_destination_connector(request: MigrationRequest) -> DestinationConnector:
    if request.destination.protocol is DestinationProtocol.IMAP:
        return ImapDestinationConnector(request.destination.connection)
    if request.destination.protocol is DestinationProtocol.CALDAV:
        return CalDavDestinationConnector(request.destination.connection)
    if request.destination.protocol is DestinationProtocol.CARDDAV:
        return CardDavDestinationConnector(request.destination.connection)
    cls = get_registry().get_destination(str(request.destination.protocol))
    if cls is not None:
        return cls(request.destination.connection)
    raise ValueError(f"Unsupported destination protocol: {request.destination.protocol}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestFactoryWithPlugin -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/groupware_migrator/connectors/factory.py tests/test_plugin_sdk.py
git commit -m "feat: factory falls back to plugin registry for unknown protocols"
```

---

### Task 5: `admin_router.py` — `GET /admin/plugins` endpoint

**Files:**
- Modify: `src/groupware_migrator/api/routers/admin_router.py`
- Modify: `tests/test_plugin_sdk.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_plugin_sdk.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from groupware_migrator.api.app import create_app
from groupware_migrator.engine.state import SQLiteStateStore, hash_password


def _authed_admin_client(app) -> TestClient:
    store: SQLiteStateStore = app.state.state_store
    store.create_user(
        email="admin@example.com",
        password_hash=hash_password("adminpass"),
        is_admin=True,
    )
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"email": "admin@example.com", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    return client


# ---------------------------------------------------------------------------
# TestAdminPluginsEndpoint
# ---------------------------------------------------------------------------

class TestAdminPluginsEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        db = str(Path(self._tmp.name) / "state.db")
        self.app = create_app(state_db_path=db)
        self.client = _authed_admin_client(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_empty_list_when_no_plugins(self):
        reg = PluginRegistry()
        with patch("groupware_migrator.api.routers.admin_router.get_registry", return_value=reg):
            resp = self.client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_returns_plugin_metadata(self):
        reg = PluginRegistry()
        reg._meta = [{
            "name": "my-plugin",
            "version": "1.2.3",
            "source_protocols": ["fake_proto"],
            "destination_protocols": [],
        }]
        with patch("groupware_migrator.api.routers.admin_router.get_registry", return_value=reg):
            resp = self.client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "my-plugin")
        self.assertEqual(data[0]["version"], "1.2.3")
        self.assertEqual(data[0]["source_protocols"], ["fake_proto"])

    def test_requires_admin(self):
        client = TestClient(self.app)
        resp = client.get("/api/admin/plugins")
        self.assertEqual(resp.status_code, 401)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestAdminPluginsEndpoint -v 2>&1 | head -15
```

Expected: 404 on `/api/admin/plugins`.

- [ ] **Step 3: Add the endpoint to `admin_router.py`**

Read `src/groupware_migrator/api/routers/admin_router.py`. Find this import line near the top:

```python
import os
```

Add after it (or alongside existing imports if already present — don't duplicate):

```python
from groupware_migrator.engine.plugin_registry import get_registry
```

Then find the last route before `return router` (currently the `ldap_status` function). Add the new route **before** `return router`:

```python
    @router.get("/admin/plugins")
    def list_plugins(_admin: dict = Depends(require_admin)) -> list:
        return get_registry().list_plugins()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.TestAdminPluginsEndpoint -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Run full plugin SDK test suite**

```bash
PYTHONPATH=src python3 -m unittest tests/test_plugin_sdk.py -v 2>&1 | tail -10
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/groupware_migrator/api/routers/admin_router.py tests/test_plugin_sdk.py
git commit -m "feat: add GET /admin/plugins endpoint listing installed connector plugins"
```

---

### Task 6: Example plugin + final integration

**Files:**
- Create: `examples/sample_plugin/pyproject.toml`
- Create: `examples/sample_plugin/src/sample_plugin/__init__.py`
- Create: `examples/sample_plugin/src/sample_plugin/connector.py`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `roadmap.html`

- [ ] **Step 1: Create example plugin package**

Create `examples/sample_plugin/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "sample-plugin"
version = "0.1.0"
description = "Example groupware-migrator connector plugin"
requires-python = ">=3.11"
dependencies = ["groupware-migrator"]

[project.entry-points."groupware_migrator.source_connectors"]
sample = "sample_plugin.connector:SampleSourceConnector"

[project.entry-points."groupware_migrator.destination_connectors"]
sample = "sample_plugin.connector:SampleDestinationConnector"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

Create `examples/sample_plugin/src/sample_plugin/__init__.py` (empty):

```python
```

Create `examples/sample_plugin/src/sample_plugin/connector.py`:

```python
"""Minimal example connector plugin for groupware-migrator.

Copy this file as a starting point. Register your connector classes
in pyproject.toml under the entry-point groups shown in this package's
pyproject.toml.
"""
from __future__ import annotations

from typing import Iterable

from groupware_migrator.sdk import (
    CollectionSnapshot,
    ConnectionConfig,
    DestinationConnector,
    MailboxSnapshot,
    SourceConnector,
    SourceMessage,
)


class SampleSourceConnector(SourceConnector):
    """Stub source connector for the 'sample' protocol."""

    protocol = "sample"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def list_mailboxes(self) -> list[MailboxSnapshot]:
        return []

    def iter_messages(
        self, mailbox: str, resume_from: str | None = None
    ) -> Iterable[SourceMessage]:
        return iter([])


class SampleDestinationConnector(DestinationConnector):
    """Stub destination connector for the 'sample' protocol."""

    protocol = "sample"

    def __init__(self, connection: ConnectionConfig) -> None:
        self._connection = connection

    def validate(self) -> None:
        pass

    def ensure_mailbox(self, mailbox: str) -> None:
        pass

    def append_message(
        self,
        mailbox: str,
        raw_message: bytes,
        *,
        flags: set[str] | None = None,
        internal_date=None,
    ) -> str | None:
        return None
```

- [ ] **Step 2: Run full test suite**

```bash
cd /home/karlo/projects/groupware-migrator
PYTHONPATH=src python3 -m unittest discover -s tests -v 2>&1 | tail -10
```

Expected: All tests pass (no regressions).

- [ ] **Step 3: Run ruff**

```bash
ruff check src tests
```

Expected: No new errors in modified files.

- [ ] **Step 4: Update `README.md`**

Read README.md. Find the API endpoints table (the one listing auth, admin, etc. routes). Add:

```markdown
| `GET` | `/api/admin/plugins` | List installed connector plugins (admin only) |
```

After the table (or in a new "Plugin SDK" section near the bottom of the README), add:

```markdown
## Plugin SDK

Third-party connectors can be installed as Python packages. Implement `SourceConnector` or `DestinationConnector` from `groupware_migrator.sdk` and register via setuptools entry points:

```toml
[project.entry-points."groupware_migrator.source_connectors"]
myproto = "my_package.connector:MySourceConnector"

[project.entry-points."groupware_migrator.destination_connectors"]
myproto = "my_package.connector:MyDestinationConnector"
```

See `examples/sample_plugin/` for a complete working example. After `pip install my-package`, specify `"protocol": "myproto"` in migration configs.
```

- [ ] **Step 5: Update `ROADMAP.md`**

In the "What's Shipped" table, add:

```markdown
| Plugin SDK | Connector plugin system — third-party packages register new protocols via entry points |
```

In the "Remaining Gaps" table, remove the `Plugin / connector SDK` row.

- [ ] **Step 6: Update `roadmap.html`**

Find the `defer-item` for the plugin SDK and remove it. Find the "All Phases Complete — What's Shipped" `foundation-grid` section and add:

```html
        <div class="foundation-item">Plugin / connector SDK — third-party connectors via entry points</div>
```

- [ ] **Step 7: Commit all**

```bash
git add examples/sample_plugin/ README.md ROADMAP.md roadmap.html
git commit -m "docs: add sample plugin, update README, ROADMAP, and roadmap.html for plugin SDK"
```

- [ ] **Step 8: Push**

```bash
git push
```
