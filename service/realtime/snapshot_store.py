"""Persistence for the latest normalized Tick snapshot."""

import json
import os
import tempfile


class TickSnapshotStore:
    """Atomically replace the compatibility ``latest.json`` snapshot."""

    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.path = os.path.join(output_dir, "latest.json")
        os.makedirs(output_dir, exist_ok=True)

    def save(self, ticks):
        if not ticks:
            return
        fd, temporary_path = tempfile.mkstemp(
            prefix=".latest-", suffix=".json", dir=self.output_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(ticks, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise


__all__ = ["TickSnapshotStore"]
