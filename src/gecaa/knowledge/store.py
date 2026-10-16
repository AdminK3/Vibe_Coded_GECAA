# SPDX-License-Identifier: Apache-2.0
"""Provision catalogue and versioned snapshots of source texts.

Every retrieval of a legal text is stored as an immutable snapshot with its
source, time and SHA-256 hash. A new hash for the same provision is a change
event that must be reviewed before it reaches the blueprints, and any past
evaluation stays traceable to the text it used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..models import Provenance, Provision


def load_provisions(path: Path) -> dict[str, Provision]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    provisions = [Provision.model_validate(item) for item in raw]
    ids = [p.id for p in provisions]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate provision ids: {sorted(dupes)}")
    return {p.id: p for p in provisions}


def sha256_text(text: str) -> str:
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@dataclass
class ChangeEvent:
    provision_id: str
    previous_sha256: str | None
    new_sha256: str
    retrieved_from: str
    retrieved_at: datetime

    @property
    def is_new(self) -> bool:
        return self.previous_sha256 is None

    def describe(self) -> str:
        if self.is_new:
            return f"{self.provision_id}: first snapshot stored"
        return (
            f"{self.provision_id}: text changed "
            f"({self.previous_sha256[:10]} -> {self.new_sha256[:10]}); expert review required"
        )


class SnapshotStore:
    """Append-only store of retrieved texts, one folder per provision."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, provision_id: str) -> Path:
        return self.root / provision_id

    def history(self, provision_id: str) -> list[dict]:
        folder = self._dir(provision_id)
        if not folder.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]

    def latest(self, provision_id: str) -> dict | None:
        items = self.history(provision_id)
        return items[-1] if items else None

    def save(self, provision_id: str, text: str, retrieved_from: str, method: str,
             text_span: str | None = None, now: datetime | None = None) -> ChangeEvent | None:
        """Store a snapshot. Returns a ChangeEvent if the text is new or changed, else None."""
        now = now or datetime.now(timezone.utc)
        digest = sha256_text(text)
        previous = self.latest(provision_id)
        if previous and previous["content_sha256"] == digest:
            return None
        folder = self._dir(provision_id)
        folder.mkdir(parents=True, exist_ok=True)
        record = {
            "provision_id": provision_id,
            "retrieved_from": retrieved_from,
            "retrieved_at": now.isoformat(),
            "retrieval_method": method,
            "text_span": text_span,
            "content_sha256": digest,
            "text": text,
        }
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        (folder / f"{stamp}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return ChangeEvent(provision_id, previous["content_sha256"] if previous else None,
                           digest, retrieved_from, now)

    def apply(self, provisions: dict[str, Provision]) -> dict[str, Provision]:
        """Return provisions with text and provenance filled from the latest snapshots."""
        out = {}
        for pid, prov in provisions.items():
            snap = self.latest(pid)
            if snap:
                prov = prov.model_copy(update={
                    "text": snap["text"],
                    "provenance": Provenance(
                        retrieved_from=snap["retrieved_from"],
                        retrieved_at=datetime.fromisoformat(snap["retrieved_at"]),
                        content_sha256=snap["content_sha256"],
                        retrieval_method=snap["retrieval_method"],
                        text_span=snap.get("text_span"),
                    ),
                })
            out[pid] = prov
        return out
