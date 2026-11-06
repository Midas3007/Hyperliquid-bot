"""Check results and the data conventions (missing/stale/zero = FAIL, never estimate)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    module: str        # "M3", "M7", ...
    rule: str          # short rule name, e.g. "whale L/S"
    ok: bool
    detail: str
    hard: bool = False  # Module 13 rule (or missing data): cannot be overridden

    def line(self) -> str:
        mark = "ok" if self.ok else ("FAIL (hard)" if self.hard else "FAIL")
        return f"{self.module} {self.rule}: {mark} - {self.detail}"


@dataclass(frozen=True)
class Datum:
    """A live value with its source and when it was observed (epoch seconds)."""

    value: object
    source: str
    ts: float | None


def usable(d: Datum | None, now: float, stale_after_min: float, zero_is_missing: bool = True):
    """Return (value, None) if the datum can be used, else (None, reason). Never estimates."""
    if d is None or d.value is None:
        return None, "missing"
    if d.ts is None:
        return None, f"no timestamp ({d.source})"
    age_min = (now - d.ts) / 60
    if age_min > stale_after_min:
        return None, f"stale ({age_min:.0f} min old, {d.source})"
    if zero_is_missing and isinstance(d.value, (int, float)) and not isinstance(d.value, bool) and d.value == 0:
        return None, f"zero reading treated as missing ({d.source})"
    return d.value, None


def na_check(module: str, rule: str, why: str) -> Check:
    """Missing or stale data fails the check, and no override can pass it (Module 13)."""
    return Check(module, rule, False, f"N/A: {why}", hard=True)


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok]


def only_soft_failures(checks: list[Check]) -> bool:
    bad = failed(checks)
    return bool(bad) and not any(c.hard for c in bad)
