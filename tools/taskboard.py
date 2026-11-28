#!/usr/bin/env python3
"""Минимальный task board для роя (п.5): очередь + lease + приёмка.

Файловое хранилище: append-only `events.jsonl`, проекция пересчитывается при загрузке.
Без сервисов; конфликт — CAS по версии; lease истекает; приёмка — не исполнителем.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = {"queued", "running", "review", "done", "failed", "blocked"}


@dataclass
class Task:
    id: str
    title: str
    owner: str = ""
    criteria: str = ""
    deadline: str = ""
    deps: list[str] = field(default_factory=list)
    status: str = "queued"
    version: int = 1
    agent: str = ""
    lease_until: float = 0.0
    result: str = ""
    verifier: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class TaskBoard:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = self.root / "events.jsonl"
        self.tasks: dict[str, Task] = {}
        self._load()

    # ---------------------------------------------------------------- журнал
    def _load(self) -> None:
        if not self.events.exists():
            return
        for line in self.events.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                self._apply(ev)

    def _append(self, ev: dict[str, Any]) -> None:
        with self.events.open("a", encoding="utf-8") as fh:
            try:
                import os
                os.fchmod(fh.fileno(), 0o600)
            except OSError:
                pass
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._apply(ev)

    def _apply(self, ev: dict[str, Any]) -> None:
        op, tid = ev.get("op"), ev.get("id")
        if not tid:
            return
        if op == "add":
            self.tasks[tid] = Task(
                id=tid, title=str(ev.get("title", "")), owner=str(ev.get("owner", "")),
                criteria=str(ev.get("criteria", "")), deadline=str(ev.get("deadline", "")),
                deps=[str(x) for x in (ev.get("deps") or [])],
            )
        elif tid in self.tasks:
            t = self.tasks[tid]
            if op == "claim":
                t.status = "running"; t.agent = str(ev.get("agent", ""))
                t.lease_until = float(ev.get("lease_until", 0)); t.version += 1
            elif op == "heartbeat":
                t.lease_until = float(ev.get("lease_until", 0)); t.version += 1
            elif op == "complete":
                t.status = "review"; t.result = str(ev.get("result", "")); t.version += 1
            elif op == "accept":
                ok = bool(ev.get("ok"))
                t.status = "done" if ok else "failed"
                t.verifier = str(ev.get("verifier", "")); t.note = str(ev.get("note", ""))
                t.version += 1
            elif op == "block":
                t.status = "blocked"; t.note = str(ev.get("note", "")); t.version += 1

    # ---------------------------------------------------------------- API
    def add(self, title: str, *, owner: str = "", criteria: str = "",
            deadline: str = "", deps: list[str] | None = None) -> dict[str, Any]:
        if not title.strip():
            return {"exit": 1, "message": "title обязателен"}
        tid = f"t_{uuid.uuid4().hex[:12]}"
        self._append({"op": "add", "id": tid, "title": title, "owner": owner,
                      "criteria": criteria, "deadline": deadline, "deps": deps or [],
                      "ts": time.time()})
        return {"exit": 0, "id": tid, "status": "queued"}

    def claim(self, tid: str, agent: str, *, ttl_min: int = 30) -> dict[str, Any]:
        t = self.tasks.get(tid)
        if t is None:
            return {"exit": 1, "message": "задача не найдена"}
        now = time.time()
        if t.status == "running" and t.lease_until > now:
            return {"exit": 2, "message": "задача занята", "agent": t.agent,
                    "lease_until": t.lease_until}
        if t.status not in {"queued", "running"}:
            return {"exit": 2, "message": f"статус {t.status} не позволяет взять"}
        unmet = [d for d in t.deps if self.tasks.get(d, Task(id=d, title="")).status != "done"]
        if unmet:
            return {"exit": 2, "message": "зависимости не выполнены", "deps": unmet}
        lease = now + ttl_min * 60
        self._append({"op": "claim", "id": tid, "agent": agent, "lease_until": lease,
                      "ts": now, "version": t.version + 1})
        return {"exit": 0, "id": tid, "status": "running", "agent": agent, "lease_until": lease}

    def heartbeat(self, tid: str, agent: str, *, ttl_min: int = 30) -> dict[str, Any]:
        t = self.tasks.get(tid)
        if t is None or t.agent != agent or t.status != "running":
            return {"exit": 1, "message": "не ваш running-таск"}
        lease = time.time() + ttl_min * 60
        self._append({"op": "heartbeat", "id": tid, "lease_until": lease, "ts": time.time()})
        return {"exit": 0, "id": tid, "lease_until": lease}

    def complete(self, tid: str, agent: str, result: str) -> dict[str, Any]:
        t = self.tasks.get(tid)
        if t is None or t.agent != agent or t.status != "running":
            return {"exit": 1, "message": "не ваш running-таск"}
        self._append({"op": "complete", "id": tid, "result": result, "ts": time.time()})
        return {"exit": 0, "id": tid, "status": "review"}

    def accept(self, tid: str, verifier: str, ok: bool, note: str = "") -> dict[str, Any]:
        t = self.tasks.get(tid)
        if t is None or t.status != "review":
            return {"exit": 1, "message": "задача не на приёмке"}
        if verifier == t.agent:
            return {"exit": 2, "message": "приёмка не может быть исполнителем/владельцем"}
        self._append({"op": "accept", "id": tid, "verifier": verifier, "ok": bool(ok),
                      "note": note, "ts": time.time()})
        return {"exit": 0, "id": tid, "status": "done" if ok else "failed"}

    def block(self, tid: str, note: str) -> dict[str, Any]:
        if tid not in self.tasks:
            return {"exit": 1, "message": "задача не найдена"}
        self._append({"op": "block", "id": tid, "note": note, "ts": time.time()})
        return {"exit": 0, "id": tid, "status": "blocked"}

    def list(self, status: str | None = None) -> dict[str, Any]:
        items = [t.as_dict() for t in self.tasks.values()
                 if status is None or t.status == status]
        items.sort(key=lambda x: x["id"])
        return {"exit": 0, "count": len(items), "tasks": items}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="taskboard")
    ap.add_argument("--root", default="/root/firm/taskboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("title"); p.add_argument("--owner", default="")
    p.add_argument("--criteria", default=""); p.add_argument("--deadline", default="")
    p.add_argument("--deps", nargs="*", default=[])
    p = sub.add_parser("claim"); p.add_argument("id"); p.add_argument("agent"); p.add_argument("--ttl", type=int, default=30)
    p = sub.add_parser("heartbeat"); p.add_argument("id"); p.add_argument("agent"); p.add_argument("--ttl", type=int, default=30)
    p = sub.add_parser("complete"); p.add_argument("id"); p.add_argument("agent"); p.add_argument("result")
    p = sub.add_parser("accept"); p.add_argument("id"); p.add_argument("verifier"); p.add_argument("--fail", action="store_true"); p.add_argument("--note", default="")
    p = sub.add_parser("block"); p.add_argument("id"); p.add_argument("note")
    p = sub.add_parser("list"); p.add_argument("--status", default=None)
    a = ap.parse_args(argv)
    b = TaskBoard(a.root)
    if a.cmd == "add": r = b.add(a.title, owner=a.owner, criteria=a.criteria, deadline=a.deadline, deps=a.deps)
    elif a.cmd == "claim": r = b.claim(a.id, a.agent, ttl_min=a.ttl)
    elif a.cmd == "heartbeat": r = b.heartbeat(a.id, a.agent, ttl_min=a.ttl)
    elif a.cmd == "complete": r = b.complete(a.id, a.agent, a.result)
    elif a.cmd == "accept": r = b.accept(a.id, a.verifier, not a.fail, a.note)
    elif a.cmd == "block": r = b.block(a.id, a.note)
    else: r = b.list(a.status)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return int(r.get("exit", 0))


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
