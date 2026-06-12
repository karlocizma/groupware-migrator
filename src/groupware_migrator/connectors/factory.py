from __future__ import annotations

from groupware_migrator.connectors.base import DestinationConnector, SourceConnector
from groupware_migrator.connectors.dav import (
    CalDavDestinationConnector,
    CalDavSourceConnector,
    CardDavDestinationConnector,
    CardDavSourceConnector,
)
from groupware_migrator.connectors.ews import EwsSourceConnector
from groupware_migrator.connectors.graph import MsGraphSourceConnector
from groupware_migrator.connectors.imap import ImapDestinationConnector, ImapSourceConnector
from groupware_migrator.connectors.pop3 import Pop3SourceConnector
from groupware_migrator.engine.plugin_registry import get_registry
from groupware_migrator.models import DestinationProtocol, MigrationRequest, SourceProtocol


def create_source_connector(request: MigrationRequest) -> SourceConnector:
    if request.source.protocol == SourceProtocol.IMAP:
        return ImapSourceConnector(request.source.connection)
    if request.source.protocol == SourceProtocol.POP3:
        return Pop3SourceConnector(request.source.connection)
    if request.source.protocol == SourceProtocol.CALDAV:
        return CalDavSourceConnector(request.source.connection)
    if request.source.protocol == SourceProtocol.CARDDAV:
        return CardDavSourceConnector(request.source.connection)
    if request.source.protocol == SourceProtocol.MSGRAPH:
        return MsGraphSourceConnector(request.source.connection)
    if request.source.protocol == SourceProtocol.EWS:
        return EwsSourceConnector(request.source.connection, workload=request.workload)
    cls = get_registry().get_source(str(request.source.protocol))
    if cls is not None:
        return cls(request.source.connection)
    raise ValueError(f"Unsupported source protocol: {request.source.protocol}")


def create_destination_connector(request: MigrationRequest) -> DestinationConnector:
    if request.destination.protocol == DestinationProtocol.IMAP:
        return ImapDestinationConnector(request.destination.connection)
    if request.destination.protocol == DestinationProtocol.CALDAV:
        return CalDavDestinationConnector(request.destination.connection)
    if request.destination.protocol == DestinationProtocol.CARDDAV:
        return CardDavDestinationConnector(request.destination.connection)
    cls = get_registry().get_destination(str(request.destination.protocol))
    if cls is not None:
        return cls(request.destination.connection)
    raise ValueError(f"Unsupported destination protocol: {request.destination.protocol}")
