"""In-memory SQS Standard queue with a redrive policy.

What it copies from the real service (the bits this study depends on):

* a received message is hidden for ``visibility_timeout`` seconds; if it is
  not deleted in that time it becomes visible again
* every receive bumps ``ApproximateReceiveCount``
* with a redrive policy, a message that has already been received
  ``maxReceiveCount`` times is moved to the DLQ on the next receive attempt
  instead of being delivered again
* delete needs the latest receipt handle; an old handle is a silent no-op
* at-least-once delivery: with a small probability a received message leaves a
  stale copy behind that is delivered again a moment later (this is where the
  no-fault duplicate floor comes from)
* ``DelaySeconds`` delivery delay

Not modelled: retention expiry (runs are minutes, retention is days), FIFO,
message size limits, cross-AZ weirdness.
"""
from __future__ import annotations

import heapq
import itertools
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class _Entry:
    key: str
    message_id: str
    body: str
    sent_at: float
    visible_at: float
    receive_count: int = 0
    receipt_handle: str | None = None
    first_receive_at: float | None = None
    shadow: bool = False


class SimQueue:
    def __init__(
        self,
        name: str,
        visibility_timeout: float,
        dlq: "SimQueue | None" = None,
        max_receive_count: int | None = None,
        rng: random.Random | None = None,
        dup_prob: float = 0.0,
        dup_delay: tuple[float, float] = (0.5, 5.0),
    ) -> None:
        if dlq is not None and not max_receive_count:
            raise ValueError("a DLQ needs a max_receive_count")
        self.name = name
        self.visibility_timeout = float(visibility_timeout)
        self.dlq = dlq
        self.max_receive_count = max_receive_count
        self.rng = rng or random.Random(0)
        self.dup_prob = dup_prob
        self.dup_delay = dup_delay

        self._entries: dict[str, _Entry] = {}
        self._heap: list[tuple[float, int, str]] = []
        self._handles: dict[str, str] = {}
        self._seq = itertools.count()
        self.counters = {
            "send_calls": 0,
            "receive_calls": 0,
            "empty_receives": 0,
            "delete_calls": 0,
            "moved_to_dlq": 0,
            "shadow_copies": 0,
        }

    # -- producer side -------------------------------------------------
    def send(self, body: str, now: float, delay: float = 0.0, message_id: str | None = None) -> str:
        self.counters["send_calls"] += 1
        mid = message_id or f"{self.rng.getrandbits(64):016x}"
        key = f"{mid}#{next(self._seq)}"
        entry = _Entry(key=key, message_id=mid, body=body, sent_at=now, visible_at=now + max(0.0, delay))
        self._add(entry)
        return mid

    def _add(self, entry: _Entry) -> None:
        self._entries[entry.key] = entry
        heapq.heappush(self._heap, (entry.visible_at, next(self._seq), entry.key))

    # -- consumer side -------------------------------------------------
    def receive(self, max_n: int, now: float) -> list[dict[str, Any]]:
        self.counters["receive_calls"] += 1
        out: list[dict[str, Any]] = []
        while self._heap and len(out) < max_n and self._heap[0][0] <= now:
            visible_at, _, key = heapq.heappop(self._heap)
            entry = self._entries.get(key)
            if entry is None or entry.visible_at != visible_at:
                continue  # stale heap slot (deleted or visibility changed)

            if self.dlq is not None and entry.receive_count >= self.max_receive_count:
                self._move_to_dlq(entry, now)
                continue

            entry.receive_count += 1
            if entry.first_receive_at is None:
                entry.first_receive_at = now
            entry.visible_at = now + self.visibility_timeout
            handle = f"{entry.key}:{entry.receive_count}:{next(self._seq)}"
            entry.receipt_handle = handle
            self._handles[handle] = entry.key
            heapq.heappush(self._heap, (entry.visible_at, next(self._seq), entry.key))
            out.append(self._record(entry, handle))

            if not entry.shadow and self.dup_prob > 0 and self.rng.random() < self.dup_prob:
                self._make_shadow(entry, now)

        if not out:
            self.counters["empty_receives"] += 1
        return out

    def _make_shadow(self, entry: _Entry, now: float) -> None:
        # a stale copy of the same message (same message id) that turns up
        # again even if the original gets deleted
        self.counters["shadow_copies"] += 1
        lo, hi = self.dup_delay
        shadow = _Entry(
            key=f"{entry.message_id}#shadow{next(self._seq)}",
            message_id=entry.message_id,
            body=entry.body,
            sent_at=entry.sent_at,
            visible_at=now + self.rng.uniform(lo, hi),
            receive_count=entry.receive_count,
            shadow=True,
        )
        self._add(shadow)

    def _move_to_dlq(self, entry: _Entry, now: float) -> None:
        self.counters["moved_to_dlq"] += 1
        del self._entries[entry.key]
        assert self.dlq is not None
        self.dlq.send(entry.body, now, message_id=entry.message_id)

    def _record(self, entry: _Entry, handle: str) -> dict[str, Any]:
        return {
            "messageId": entry.message_id,
            "receiptHandle": handle,
            "body": entry.body,
            "attributes": {
                "ApproximateReceiveCount": str(entry.receive_count),
                "SentTimestamp": str(int(entry.sent_at * 1000)),
                "ApproximateFirstReceiveTimestamp": str(int((entry.first_receive_at or 0) * 1000)),
            },
            "messageAttributes": {},
            "eventSource": "aws:sqs",
            "eventSourceARN": f"arn:aws:sqs:local:000000000000:{self.name}",
        }

    def delete(self, receipt_handle: str) -> bool:
        self.counters["delete_calls"] += 1
        key = self._handles.pop(receipt_handle, None)
        entry = self._entries.get(key) if key else None
        if entry is None or entry.receipt_handle != receipt_handle:
            return False  # old handle -> SQS just ignores it
        del self._entries[key]
        return True

    def change_visibility(self, receipt_handle: str, new_timeout: float, now: float) -> bool:
        key = self._handles.get(receipt_handle)
        entry = self._entries.get(key) if key else None
        if entry is None or entry.receipt_handle != receipt_handle:
            return False
        entry.visible_at = now + float(new_timeout)
        heapq.heappush(self._heap, (entry.visible_at, next(self._seq), entry.key))
        return True

    # -- attributes ------------------------------------------------------
    def depth(self, now: float) -> dict[str, int]:
        """Roughly ApproximateNumberOfMessages / NotVisible / Delayed."""
        visible = inflight = delayed = 0
        for e in self._entries.values():
            if e.visible_at <= now:
                visible += 1
            elif e.receive_count > 0:
                inflight += 1
            else:
                delayed += 1
        return {"visible": visible, "inflight": inflight, "delayed": delayed}

    def next_visible_at(self) -> float | None:
        while self._heap:
            visible_at, _, key = self._heap[0]
            entry = self._entries.get(key)
            if entry is None or entry.visible_at != visible_at:
                heapq.heappop(self._heap)
                continue
            return visible_at
        return None

    def bodies(self) -> list[str]:
        return [e.body for e in self._entries.values()]

    def __len__(self) -> int:
        return len(self._entries)
