"""Parquet lake with per-pull manifests and snapshot hashing.

Layout::

    lake/{source}/{dataset}/date=YYYY-MM-DD/part-0.parquet   (daily datasets)
    lake/{source}/{dataset}/static/part-0.parquet             (reference datasets)
    lake/_manifests/{source}/{dataset}/{manifest_hash}.json
    lake/_snapshots/{snapshot_hash}.json

A manifest records query args, row count, per-file SHA-256, pull time, SDK version and the
schema version. A snapshot is a set of manifests (one per dataset); its hash is what a run
references, never "latest".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from simp.data.lake.contracts import CONTRACTS, check_contract
from simp.errors import DataMissingError
from simp.settings import get_settings


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Manifest:
    source: str
    dataset: str
    schema_version: int
    query_args: dict[str, Any]
    row_count: int
    files: dict[str, str]  # relative path -> sha256
    pulled_at: str
    sdk_version: str
    data_label: str  # SYNTHETIC | CRSP | BMLL | DERIVED

    def content_hash(self) -> str:
        payload = {
            "source": self.source,
            "dataset": self.dataset,
            "schema_version": self.schema_version,
            "query_args": self.query_args,
            "row_count": self.row_count,
            "files": dict(sorted(self.files.items())),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def to_json(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["manifest_hash"] = self.content_hash()
        return d


class Lake:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or get_settings().lake_root)

    # ---------- paths
    def dataset_dir(self, source: str, dataset: str) -> Path:
        return self.root / source / dataset

    def _manifest_dir(self, source: str, dataset: str) -> Path:
        return self.root / "_manifests" / source / dataset

    # ---------- write
    def write(
        self,
        source: str,
        dataset: str,
        df: pl.DataFrame,
        *,
        date_col: str | None,
        query_args: dict[str, Any],
        sdk_version: str,
        data_label: str,
    ) -> Manifest:
        contract = CONTRACTS[(source, dataset)]
        check_contract(df, contract)
        base = self.dataset_dir(source, dataset)
        files: dict[str, str] = {}
        if date_col is None:
            target = base / "static" / "part-0.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(target, statistics=True)
            files[str(target.relative_to(self.root))] = sha256_file(target)
        else:
            df = df.sort(date_col)
            for (d,), part in df.group_by([date_col], maintain_order=True):
                assert isinstance(d, date)  # noqa: S101 - type narrowing only
                target = base / f"date={d.isoformat()}" / "part-0.parquet"
                target.parent.mkdir(parents=True, exist_ok=True)
                part.write_parquet(target, statistics=True)
                files[str(target.relative_to(self.root))] = sha256_file(target)
        m = Manifest(
            source=source,
            dataset=dataset,
            schema_version=contract.version,
            query_args=query_args,
            row_count=df.height,
            files=files,
            pulled_at=datetime.now(UTC).isoformat(),
            sdk_version=sdk_version,
            data_label=data_label,
        )
        mdir = self._manifest_dir(source, dataset)
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / f"{m.content_hash()}.json").write_text(json.dumps(m.to_json(), indent=1, default=str))
        return m

    # ---------- manifests and snapshots
    def manifests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        mroot = self.root / "_manifests"
        if not mroot.exists():
            return out
        for p in sorted(mroot.glob("*/*/*.json")):
            out.append(json.loads(p.read_text()))
        return out

    def latest_manifest(self, source: str, dataset: str) -> dict[str, Any] | None:
        ms = [m for m in self.manifests() if m["source"] == source and m["dataset"] == dataset]
        if not ms:
            return None
        return max(ms, key=lambda m: m["pulled_at"])

    def create_snapshot(self, datasets: Iterable[tuple[str, str]]) -> str:
        chosen: dict[str, str] = {}
        for source, dataset in datasets:
            m = self.latest_manifest(source, dataset)
            if m is None:
                raise DataMissingError(f"no manifest for {source}/{dataset}; run `simp data pull` first")
            chosen[f"{source}/{dataset}"] = m["manifest_hash"]
        h = hashlib.sha256(json.dumps(chosen, sort_keys=True).encode()).hexdigest()
        sdir = self.root / "_snapshots"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / f"{h}.json").write_text(
            json.dumps({"snapshot_hash": h, "manifests": chosen, "created_at": datetime.now(UTC).isoformat()}, indent=1)
        )
        return h

    def snapshot(self, snapshot_hash: str) -> dict[str, Any]:
        p = self.root / "_snapshots" / f"{snapshot_hash}.json"
        if not p.exists():
            raise DataMissingError(f"data snapshot {snapshot_hash} not found in lake")
        data: dict[str, Any] = json.loads(p.read_text())
        return data

    def snapshots(self) -> list[dict[str, Any]]:
        sdir = self.root / "_snapshots"
        if not sdir.exists():
            return []
        return sorted((json.loads(p.read_text()) for p in sdir.glob("*.json")), key=lambda s: s["created_at"])

    def verify_snapshot(self, snapshot_hash: str) -> None:
        """Re-hash every file of a snapshot; raises if anything changed on disk."""
        snap = self.snapshot(snapshot_hash)
        for key, mh in snap["manifests"].items():
            source, dataset = key.split("/")
            p = self._manifest_dir(source, dataset) / f"{mh}.json"
            if not p.exists():
                raise DataMissingError(f"manifest {mh} for {key} missing")
            m = json.loads(p.read_text())
            for rel, digest in m["files"].items():
                f = self.root / rel
                if not f.exists() or sha256_file(f) != digest:
                    raise DataMissingError(f"lake file {rel} is missing or changed since manifest {mh}")

    def manifest_for(self, snapshot_hash: str, source: str, dataset: str) -> dict[str, Any]:
        snap = self.snapshot(snapshot_hash)
        mh = snap["manifests"].get(f"{source}/{dataset}")
        if mh is None:
            raise DataMissingError(f"snapshot {snapshot_hash[:12]} has no {source}/{dataset}")
        data: dict[str, Any] = json.loads((self._manifest_dir(source, dataset) / f"{mh}.json").read_text())
        return data

    # ---------- read
    def read(self, snapshot_hash: str, source: str, dataset: str) -> pl.DataFrame:
        m = self.manifest_for(snapshot_hash, source, dataset)
        paths = [str(self.root / rel) for rel in sorted(m["files"])]
        if not paths:
            raise DataMissingError(f"{source}/{dataset} has no files")
        return pl.read_parquet(paths)

    def partition_dates(self, source: str, dataset: str) -> list[date]:
        base = self.dataset_dir(source, dataset)
        if not base.exists():
            return []
        return sorted(date.fromisoformat(p.name.split("=", 1)[1]) for p in base.glob("date=*"))
