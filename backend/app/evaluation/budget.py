"""Per-experiment admission includes unresolved worst-case provider spend."""

from contextvars import ContextVar
from decimal import Decimal
import threading
import uuid

current_budget = ContextVar("evaluation_budget", default=None)


class ExperimentBudget:
    def __init__(self, cap):
        self.cap = Decimal(str(cap))
        if not self.cap.is_finite() or self.cap <= 0:
            raise ValueError("A positive explicit USD cap is required")
        self.entries = {}
        self.lock = threading.Lock()

    def claim(self, ceiling):
        value = Decimal(str(ceiling))
        if not value.is_finite() or value < 0:
            raise ValueError("Invalid cost ceiling")
        with self.lock:
            if sum(self.entries.values(), Decimal(0)) + value > self.cap:
                raise ValueError(
                    "Evaluation USD cap exhausted, including pending calls"
                )
            key = str(uuid.uuid4())
            self.entries[key] = value
            return key

    def settle(self, key, cost):
        if cost is None:
            return
        value = Decimal(str(cost))
        if not value.is_finite() or value < 0:
            raise ValueError("Invalid evaluation cost")
        with self.lock:
            self.entries[key] = value

    @property
    def committed(self):
        with self.lock:
            return sum(self.entries.values(), Decimal(0))
