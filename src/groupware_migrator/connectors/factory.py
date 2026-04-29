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
    raise ValueError(f"Unsupported source protocol: {request.source.protocol}")


def create_destination_connector(request: MigrationRequest) -> DestinationConnector:
    if request.destination.protocol is DestinationProtocol.IMAP:
        return ImapDestinationConnector(request.destination.connection)
    if request.destination.protocol is DestinationProtocol.CALDAV:
        return CalDavDestinationConnector(request.destination.connection)
    if request.destination.protocol is DestinationProtocol.CARDDAV:
        return CardDavDestinationConnector(request.destination.connection)
    raise ValueError(f"Unsupported destination protocol: {request.destination.protocol}")
