"""Persistence for the latest normalized Tick snapshot."""

import json
import os
import tempfile


class TickSnapshotStore:
    """Atomically replace the compatibility ``latest.json`` snapshot."""

    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.path = os.path.join(output_dir, "latest.json")
        self.metadata_path = f"{self.path}.meta"
        os.makedirs(output_dir, exist_ok=True)

    def save(self, ticks, session_identity=None):
        """Replace the snapshot and bind it to the producing session.

        ``latest.json`` remains the compatibility mapping consumed by older
        tooling.  The adjacent metadata file carries the opaque session ticket
        so a restarted worker cannot leave a prior account snapshot readable.
        """
        if session_identity is None:
            identity = None
        else:
            identity = self._normalize_identity(session_identity)
            if identity is None:
                self._remove_snapshot()
                raise ValueError("Invalid session identity")
        previous = self._read_identity()
        if identity is not None and previous is not None and previous != identity:
            self._remove_snapshot()
        if not ticks:
            if identity is not None:
                self._remove_snapshot()
            return
        if identity is not None and not self._ticks_match_identity(ticks, identity):
            self._remove_snapshot()
            raise ValueError("Tick snapshot has invalid or mismatched session identity")
        fd, temporary_path = tempfile.mkstemp(
            prefix=".latest-", suffix=".json", dir=self.output_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(ticks, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            if identity is not None:
                try:
                    self._atomic_write_metadata(identity)
                except BaseException:
                    # A data file without a matching identity must never be
                    # mistaken for a valid snapshot after a partial write.
                    try:
                        os.unlink(self.metadata_path)
                    except FileNotFoundError:
                        pass
                    raise
            elif os.path.exists(self.metadata_path):
                os.unlink(self.metadata_path)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _normalize_identity(identity):
        if not isinstance(identity, dict):
            return None
        required = ('session_fingerprint', 'session_generation', 'session_epoch')
        if any(key not in identity for key in required):
            return None
        if (not isinstance(identity['session_fingerprint'], str)
                or not identity['session_fingerprint'].strip()
                or isinstance(identity['session_generation'], bool)
                or not isinstance(identity['session_generation'], int)
                or identity['session_generation'] < 1
                or not isinstance(identity['session_epoch'], str)
                or not identity['session_epoch'].strip()):
            return None
        return {key: identity[key] for key in required}

    def _read_identity(self):
        try:
            with open(self.metadata_path, encoding='utf-8') as source:
                identity = self._normalize_identity(json.load(source))
        except FileNotFoundError:
            return None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._remove_snapshot()
            return None
        if identity is None:
            # A metadata file that is present but malformed cannot authorize
            # the adjacent data file.  Remove both before continuing.
            self._remove_snapshot()
            return None
        return identity

    @classmethod
    def _ticks_match_identity(cls, ticks, identity):
        if not isinstance(ticks, dict):
            return False
        for event in ticks.values():
            if cls._normalize_identity(event) != identity:
                return False
        return True

    def _remove_snapshot(self):
        for path in (self.path, self.metadata_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _atomic_write_metadata(self, identity):
        fd, temporary_path = tempfile.mkstemp(
            prefix='.latest-meta-', suffix='.json', dir=self.output_dir
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(identity, handle, separators=(',', ':'), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.metadata_path)
            temporary_path = None
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


__all__ = ["TickSnapshotStore"]
