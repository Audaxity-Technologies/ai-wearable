"""Blob storage for audio.

`LocalBlobStore` writes under DATA_DIR/audio/<session_id>/<segment_id>.<ext>.
An R2/S3 implementation satisfying the same Protocol replaces it at deploy
time without touching callers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.errors import NotFoundError

#: Accepted upload types, mapped to the extension used on disk. Groq accepts
#: all of these; the extension matters because it is sent as the filename.
MIME_EXTENSIONS: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
}


def extension_for(mime_type: str) -> str | None:
    return MIME_EXTENSIONS.get(mime_type.split(";")[0].strip().lower())


def blob_key(session_id: str, segment_id: str, extension: str) -> str:
    return f"{session_id}/{segment_id}.{extension}"


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        root = self._root.resolve()
        # Guard against a crafted key escaping the audio directory.
        if not path.is_relative_to(root):
            raise ValueError(f"Blob key escapes storage root: {key!r}")
        return path

    async def put(self, key: str, data: bytes) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFoundError(f"Audio blob not found: {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        await asyncio.to_thread(path.unlink)
        return True

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()
