from __future__ import annotations

from pathlib import Path

from app import store_fs
from app.schemas import CaptureModel


class FileCaptureRepository:
    def init(self) -> None:
        store_fs.init_db()

    def create(self, **kwargs) -> CaptureModel:
        return store_fs.create_capture(**kwargs)

    def update(self, capture_id: str, **updates) -> CaptureModel | None:
        return store_fs.update_capture(capture_id, **updates)

    def get(self, capture_id: str) -> CaptureModel | None:
        return store_fs.get_capture(capture_id)

    def list(self, limit: int = 50, client_ip: str | None = None) -> list[CaptureModel]:
        return store_fs.list_captures(limit=limit, client_ip=client_ip)

    def list_pending_ids(self) -> list[str]:
        return store_fs.list_pending_capture_ids()

    def clear_completed(self) -> int:
        return store_fs.clear_completed_captures()

    def delete(self, capture_id: str) -> bool:
        return store_fs.delete_capture(capture_id)

    def find_by_url(self, url: str, *, statuses: tuple[str, ...] = ("done", "processing", "queued")) -> CaptureModel | None:
        return store_fs.find_capture_by_url(url, statuses=statuses)

    def find_by_source_identity(
        self,
        platform: str,
        source_item_id: str,
        *,
        statuses: tuple[str, ...] = ("done", "processing", "queued"),
    ) -> CaptureModel | None:
        return store_fs.find_capture_by_source_identity(platform, source_item_id, statuses=statuses)

    def capture_workspace(self, capture_id: str) -> Path:
        return store_fs.capture_workspace(capture_id)


_REPOSITORY = FileCaptureRepository()


def get_capture_repository() -> FileCaptureRepository:
    return _REPOSITORY
