"""Automated SQLite -> S3 backups.

A consistent snapshot is taken with `VACUUM INTO` (safe while the bot keeps writing, since the DB
runs in WAL + autocommit), gzipped, and uploaded to a PRIVATE S3 bucket. Old backups are pruned to
a retention count. Ships dark: bot.py only wires this up when BACKUP_S3_BUCKET is configured, so
boto3 is imported lazily and the feature is a no-op otherwise.

Design notes:
- Runs in the same process as the bot, so the snapshot goes through the one serialized db.py
  connection -- no cross-process SQLite lock contention.
- boto3's calls are blocking, so they run in the default executor to keep the event loop free.
- Backup object keys are timestamped (UTC, zero-padded), so lexicographic sort == chronological,
  which makes retention pruning a simple "delete all but the newest N".
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import io
import logging
import os
import tempfile

log = logging.getLogger("holmesbot.backup")


class BackupService:
    def __init__(
        self,
        pool,
        *,
        bucket: str,
        prefix: str = "db-backups/",
        retention: int = 30,
    ) -> None:
        self.pool = pool
        self.bucket = bucket
        self.prefix = (prefix.strip("/") + "/") if prefix.strip("/") else ""
        self.retention = max(1, retention)
        self._client = None  # lazy boto3 client (dep only needed when backups are enabled)

    def _s3(self):
        if self._client is None:
            import boto3  # imported here so boto3 is optional unless backups are configured

            self._client = boto3.client("s3")  # region + creds from the standard env chain
        return self._client

    async def run_once(self) -> str:
        """Snapshot -> gzip -> upload -> prune. Returns the uploaded S3 key. Raises on failure."""
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        key = f"{self.prefix}holmesbot-{ts}.sqlite3.gz"
        data = await self._snapshot_gz()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._upload, key, data)
        await loop.run_in_executor(None, self._prune)
        log.info("db backup uploaded: s3://%s/%s (%d bytes gzipped)", self.bucket, key, len(data))
        return key

    async def _snapshot_gz(self) -> bytes:
        """A consistent snapshot of the live DB, gzipped in memory. `VACUUM INTO` writes a fresh,
        defragmented copy of the committed state even while writes continue."""
        with tempfile.TemporaryDirectory(prefix="holmesbot-bak-") as tmp:
            snap = os.path.join(tmp, "snapshot.sqlite3")
            # `snap` is our own temp path (no user input), safe to inline as a SQL string literal.
            await self.pool.execute(f"VACUUM INTO '{snap}'")
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._gzip_file, snap)

    @staticmethod
    def _gzip_file(path: str) -> bytes:
        buf = io.BytesIO()
        # mtime=0 keeps the gzip header deterministic (nice for byte-identical no-change backups).
        with open(path, "rb") as f, gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            while chunk := f.read(1 << 20):
                gz.write(chunk)
        return buf.getvalue()

    def _upload(self, key: str, data: bytes) -> None:
        self._s3().put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType="application/gzip"
        )

    def _prune(self) -> None:
        """Delete all but the newest `retention` backups under the prefix."""
        s3 = self._s3()
        objs: list[dict] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            objs.extend(resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        objs.sort(key=lambda o: o["Key"])  # timestamped keys sort chronologically
        excess = objs[: -self.retention] if len(objs) > self.retention else []
        if excess:
            s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in excess], "Quiet": True},
            )
            log.info("pruned %d old db backup(s), keeping newest %d", len(excess), self.retention)
