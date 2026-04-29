from __future__ import annotations

from groupware_migrator.connectors.base import SourceConnector
from groupware_migrator.models import (
    CollectionSnapshot,
    MigrationPlan,
    MigrationPlanItem,
    MigrationRequest,
    SourceProtocol,
    SyncMode,
)

def _normalize_collection_name(collection: str) -> str:
    return collection.strip().strip("/")


def _collection_match_variants(collection: str) -> set[str]:
    normalized = _normalize_collection_name(collection)
    variants = {
        normalized.casefold(),
        normalized.replace("/", ".").casefold(),
        normalized.replace(".", "/").casefold(),
    }
    return {variant for variant in variants if variant}


def _join_collections(root: str, collection: str) -> str:
    root_normalized = _normalize_collection_name(root)
    collection_normalized = _normalize_collection_name(collection)
    if not root_normalized:
        return collection_normalized or "INBOX"
    if not collection_normalized:
        return root_normalized
    return f"{root_normalized}/{collection_normalized}"


class MigrationPlanner:
    def build_plan(
        self,
        request: MigrationRequest,
        source_connector: SourceConnector,
        *,
        incremental_cursors: dict[str, str] | None = None,
    ) -> MigrationPlan:
        snapshots = source_connector.list_collections()
        snapshots = self._filter_collections(request, snapshots)
        cursor_lookup = incremental_cursors or {}
        items: list[MigrationPlanItem] = []
        for snapshot in snapshots:
            estimated_items = max(snapshot.estimated_items, 0)
            if request.options.sync_mode is SyncMode.INCREMENTAL:
                resume_from = cursor_lookup.get(snapshot.name)
                if resume_from:
                    pending_estimate = source_connector.estimate_pending_items(
                        snapshot.name,
                        resume_from,
                    )
                    if pending_estimate is not None:
                        estimated_items = max(int(pending_estimate), 0)

            items.append(
                MigrationPlanItem(
                    source_collection=snapshot.name,
                    destination_collection=self._resolve_destination_collection(
                        request=request,
                        source_collection=snapshot.name,
                    ),
                    estimated_items=estimated_items,
                )
            )
        return MigrationPlan(items=items)

    def _filter_collections(
        self,
        request: MigrationRequest,
        snapshots: list[CollectionSnapshot],
    ) -> list[CollectionSnapshot]:
        if request.source.include_collections:
            requested: set[str] = set()
            for collection in request.source.include_collections:
                requested.update(_collection_match_variants(collection))
            return [
                snapshot
                for snapshot in snapshots
                if _collection_match_variants(snapshot.name).intersection(requested)
            ]
        return snapshots

    def _resolve_destination_collection(
        self,
        *,
        request: MigrationRequest,
        source_collection: str,
    ) -> str:
        if request.source.protocol is SourceProtocol.POP3:
            mapped_collection = request.options.pop3_destination_mailbox
        else:
            mapped_collection = request.folder_mapping.get(
                source_collection,
                source_collection,
            )
        return _join_collections(request.destination.root_collection, mapped_collection)
