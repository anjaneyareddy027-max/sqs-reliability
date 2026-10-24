"""Virtual time helpers for the simulator."""
from __future__ import annotations

from common.faults import LambdaTimeout


class VirtualClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def time(self) -> float:
        return self.now

    def advance_to(self, t: float) -> None:
        # events can be scheduled for "now", never for the past
        if t + 1e-9 < self.now:
            raise ValueError(f"clock cannot go backwards ({t} < {self.now})")
        self.now = max(self.now, float(t))


class SimSleeper:
    """Tracks how much virtual time one Lambda invocation has used.

    Anything that "takes time" inside the handler (a record being parsed, a
    DynamoDB write, the datastore_timeout hang) calls ``sleep``. Going over the
    function timeout raises LambdaTimeout, which is what the Lambda runtime
    would do to a real invocation.
    """

    def __init__(self, start: float, budget_s: float) -> None:
        self.start = float(start)
        self.budget = float(budget_s)
        self.used = 0.0

    def sleep(self, seconds: float) -> None:
        self.used += max(0.0, float(seconds))
        if self.used > self.budget:
            self.used = self.budget
            raise LambdaTimeout(f"invocation exceeded {self.budget:.1f}s")

    def now(self) -> float:
        return self.start + self.used

    def remaining(self) -> float:
        return max(0.0, self.budget - self.used)
