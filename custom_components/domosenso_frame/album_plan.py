"""Decide what a sync run downloads and deletes."""
from __future__ import annotations

from dataclasses import dataclass

from .album_share import RemoteItem

# Whether a changed photo gets a new Synology version number (pending a live check).
VERSION_TRACKS_CONTENT = False


class IncompleteListing(Exception):
    """The remote listing does not match the reported item count."""


@dataclass(frozen=True)
class SyncPlan:
    download: list[RemoteItem]
    delete: list[str]
    hold: bool


def plan_sync(local: dict[int, str], remote: list[RemoteItem], item_count: int, confirm: bool) -> SyncPlan:
    if len(remote) != item_count:
        raise IncompleteListing(f'Got {len(remote)} of {item_count} items')
    photos = {i.id: i for i in remote if i.type == 'photo'}
    delete = [name for item_id, name in local.items() if item_id not in photos]
    if local and not confirm and (not photos or len(delete) > len(local) / 2):
        return SyncPlan([], [], True)
    return SyncPlan([i for i in photos.values() if i.id not in local], delete, False)
