"""Pending actions and traces, as JSON files under data/runtime/ (gitignored).
Small on purpose: a Space has one process; swap for a DB if the client needs more."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .config import RUNTIME
from .schema import PendingAction


class Store:
    def __init__(self, root: Path | str = RUNTIME) -> None:
        self.root = Path(root)
        (self.root / "pending").mkdir(parents=True, exist_ok=True)
        (self.root / "traces").mkdir(parents=True, exist_ok=True)

    # --- pending actions -------------------------------------------------------
    def queue(self, thread_id: str, tool: str, args: dict) -> PendingAction:
        a = PendingAction(action_id=f"act_{uuid.uuid4().hex[:10]}", thread_id=thread_id, tool=tool, args=args)
        self._write(a)
        return a

    def get(self, action_id: str) -> PendingAction | None:
        p = self.root / "pending" / f"{action_id}.json"
        return PendingAction.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def update(self, action: PendingAction) -> None:
        self._write(action)

    def pending(self, thread_id: str | None = None) -> list[PendingAction]:
        out = []
        for p in sorted((self.root / "pending").glob("*.json")):
            a = PendingAction.model_validate_json(p.read_text(encoding="utf-8"))
            if a.status == "pending" and (thread_id is None or a.thread_id == thread_id):
                out.append(a)
        return out

    def _write(self, a: PendingAction) -> None:
        (self.root / "pending" / f"{a.action_id}.json").write_text(a.model_dump_json(indent=1), encoding="utf-8")

    # --- traces ---------------------------------------------------------------
    def trace_path(self, thread_id: str) -> Path:
        return self.root / "traces" / f"{thread_id}.jsonl"

    def append_trace(self, thread_id: str, event: dict) -> None:
        with self.trace_path(thread_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def read_trace(self, thread_id: str) -> list[dict]:
        p = self.trace_path(thread_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
