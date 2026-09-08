from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class BudgetConfig:
    max_iters: int = 6
    max_tokens: int = 6000
    max_cost: float = 0.02
    max_wall_clock_ms: int = 45_000


@dataclass
class BudgetTracker:
    config: BudgetConfig
    iterations: int = 0
    tokens_total: int = 0
    cost_total: float = 0.0
    _start: float = field(default_factory=time.monotonic)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def record_iteration(self, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        self.iterations += 1
        self.tokens_total += prompt_tokens + completion_tokens
        self.cost_total += cost

    def add_side_cost(self, tokens: int, cost: float) -> None:
        """For model calls that support an iteration (e.g. context-window
        summarization) but aren't themselves an agent decision step, so
        they contribute to tokens/cost/budget checks without incrementing
        the iteration count."""
        self.tokens_total += tokens
        self.cost_total += cost

    def check(self) -> str | None:
        """Returns a termination reason string if any budget has been
        exceeded, else None. Order matters only for which reason is
        reported when multiple fire in the same tick."""
        if self.iterations >= self.config.max_iters:
            return "max_iterations"
        if self.tokens_total >= self.config.max_tokens:
            return "max_tokens"
        if self.cost_total >= self.config.max_cost:
            return "max_cost"
        if self.elapsed_ms() >= self.config.max_wall_clock_ms:
            return "wall_clock"
        return None

    def snapshot(self) -> dict:
        return {
            "iterations": self.iterations,
            "tokens_total": self.tokens_total,
            "cost_total": round(self.cost_total, 6),
            "elapsed_ms": self.elapsed_ms(),
        }
