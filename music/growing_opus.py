"""Read complete Opus packets while their cache file is still being encoded."""

import io
import os
import threading
import uuid


class GrowingOpusFile:
    """One staging file shared by the encoder and independent voice readers."""

    def __init__(self, final_path):
        self.final_path = final_path
        self.staged_path = f"{final_path}.{uuid.uuid4().hex}.tmp.opus"
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._published = False

    def ready(self):
        # Leave roughly a second of encoded audio ahead of the voice thread.
        with self._lock:
            try:
                return os.path.getsize(self.staged_path) >= 32 * 1024
            except FileNotFoundError:
                return False

    def publish(self):
        # A Windows reader must close the staging file before os.replace.
        with self._lock:
            os.replace(self.staged_path, self.final_path)
            self._published = True
            self._finished.set()

    def finish(self):
        self._finished.set()

    def read_at(self, offset, buffer):
        while True:
            with self._lock:
                if self._finished.is_set() and not self._published:
                    raise OSError("Cache encoder did not finish")
                path = self.final_path if self._published else self.staged_path
                with open(path, "rb", buffering=0) as stream:
                    stream.seek(offset)
                    count = stream.readinto(buffer)
                if count or self._published:
                    return count
            self._finished.wait(0.025)

    def open_reader(self):
        return io.BufferedReader(_OpusReader(self), buffer_size=65536)


class _OpusReader(io.RawIOBase):
    def __init__(self, build):
        super().__init__()
        self.build = build
        self.offset = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        count = self.build.read_at(self.offset, buffer)
        self.offset += count
        return count
