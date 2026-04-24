"""Cron job storage and schedule evaluation for Prax.

- ``CronJob`` / ``CronStore``: read/write ``.prax/cron.yaml``.
- ``is_due(expr, dt)``: minimal 5-field crontab evaluator with ``*``, ``*/N``,
  ``a,b,c``, ``a-b``, ``a-b/s`` support — no external dependency.

The evaluator is kept deliberately small; users who need niche extensions
(``@reboot``, ``L``, seconds) should reach for a mature scheduler instead of
patching this one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class InvalidScheduleError(ValueError):
    """Raised when a cron expression cannot be parsed or validated."""


class DuplicateJobError(ValueError):
    """Raised when adding a job with a name that already exists."""


class UnknownJobError(KeyError):
    """Raised when a job lookup by name fails."""

    def __str__(self) -> str:
        if self.args:
            return f"cron job {self.args[0]!r} not found"
        return "cron job not found"


_FIELD_BOUNDS = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (7 == Sunday == 0)
)
_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")


def _parse_field(text: str, lo: int, hi: int, name: str) -> set[int]:
    """Parse one cron field into the concrete set of values it matches."""
    if not text:
        raise InvalidScheduleError(f"{name} field is empty")

    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise InvalidScheduleError(f"{name} has an empty element")

        step: int | None = None
        if "/" in part:
            head, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError as e:
                raise InvalidScheduleError(
                    f"{name} has non-integer step {step_raw!r}"
                ) from e
            if step <= 0:
                raise InvalidScheduleError(f"{name} step must be >= 1, got {step}")
            part = head

        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            start_raw, end_raw = part.split("-", 1)
            try:
                start = int(start_raw)
                end = int(end_raw)
            except ValueError as e:
                raise InvalidScheduleError(f"{name} has malformed range {part!r}") from e
            if start > end:
                raise InvalidScheduleError(
                    f"{name} has reversed range {start}-{end}"
                )
        else:
            try:
                v = int(part)
            except ValueError as e:
                raise InvalidScheduleError(f"{name} has invalid value {part!r}") from e
            start = end = v

        if start < lo or end > hi:
            raise InvalidScheduleError(
                f"{name} value {start}-{end} out of range {lo}-{hi}"
            )

        step = step or 1
        values.update(range(start, end + 1, step))

    return values


def _parse(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    """Parse full cron expression.

    Returns (minutes, hours, doms, months, dows, dom_restricted, dow_restricted).
    ``*_restricted`` flags enable the classic cron OR-rule between dom and dow.
    """
    if not isinstance(expr, str):
        raise InvalidScheduleError("schedule must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise InvalidScheduleError(
            f"expected 5 fields (minute hour dom month dow), got {len(fields)}: {expr!r}"
        )

    parsed = []
    for text, (lo, hi), name in zip(fields, _FIELD_BOUNDS, _FIELD_NAMES):
        parsed.append(_parse_field(text, lo, hi, name))

    dow_values = parsed[4]
    # Cron day-of-week: 0 and 7 both mean Sunday; collapse 7 → 0.
    if 7 in dow_values:
        dow_values = (dow_values - {7}) | {0}

    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"

    return parsed[0], parsed[1], parsed[2], parsed[3], dow_values, dom_restricted, dow_restricted


def validate_schedule(expr: str) -> None:
    """Raise InvalidScheduleError if the expression cannot be used at runtime."""
    _parse(expr)


def is_due(expr: str, moment: datetime) -> bool:
    """Return True iff ``moment`` satisfies the cron ``expr`` at minute resolution."""
    minutes, hours, doms, months, dows, dom_restricted, dow_restricted = _parse(expr)

    if moment.minute not in minutes:
        return False
    if moment.hour not in hours:
        return False
    if moment.month not in months:
        return False

    cron_dow = (moment.weekday() + 1) % 7  # datetime: Mon=0..Sun=6; cron: Sun=0..Sat=6
    dom_match = moment.day in doms
    dow_match = cron_dow in dows

    # Classic cron: when BOTH dom and dow are restricted, it's OR; otherwise AND.
    if dom_restricted and dow_restricted:
        return dom_match or dow_match
    return dom_match and dow_match


# ── Job storage ──────────────────────────────────────────────────────────────


@dataclass
class CronJob:
    name: str
    schedule: str
    prompt: str
    session_id: str | None = None
    model: str | None = None
    notify_on: list[str] = field(default_factory=list)
    notify_channel: str | None = None

    def validate(self) -> None:
        if not self.name:
            raise ValueError("cron job name must be non-empty")
        if not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"cron job name {self.name!r} must be alphanumeric plus '-' or '_'"
            )
        if not self.prompt:
            raise ValueError("cron job prompt must be non-empty")
        validate_schedule(self.schedule)
        for trigger in self.notify_on:
            if trigger not in ("success", "failure"):
                raise ValueError(
                    f"notify_on entries must be 'success' or 'failure', got {trigger!r}"
                )

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "name": self.name,
            "schedule": self.schedule,
            "prompt": self.prompt,
        }
        if self.session_id:
            data["session_id"] = self.session_id
        if self.model:
            data["model"] = self.model
        if self.notify_on:
            data["notify_on"] = list(self.notify_on)
        if self.notify_channel:
            data["notify_channel"] = self.notify_channel
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "CronJob":
        try:
            job = cls(
                name=str(raw["name"]),
                schedule=str(raw["schedule"]),
                prompt=str(raw["prompt"]),
                session_id=raw.get("session_id"),
                model=raw.get("model"),
                notify_on=list(raw.get("notify_on", [])),
                notify_channel=raw.get("notify_channel"),
            )
        except KeyError as e:
            raise ValueError(f"cron job missing required field {e.args[0]!r}") from e
        job.validate()
        return job


class CronStore:
    """CRUD for ``.prax/cron.yaml``."""

    def __init__(self, cwd: str):
        self._path = Path(cwd) / ".prax" / "cron.yaml"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[CronJob]:
        if not self._path.exists():
            return []
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"cron.yaml is not valid YAML: {e}") from e
        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise ValueError("cron.yaml 'jobs' must be a list")
        return [CronJob.from_dict(j) for j in raw_jobs if isinstance(j, dict)]

    def save(self, jobs: list[CronJob]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": [j.to_dict() for j in jobs]}
        self._path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))

    def add(self, job: CronJob) -> None:
        job.validate()
        jobs = self.load()
        if any(existing.name == job.name for existing in jobs):
            raise DuplicateJobError(f"cron job {job.name!r} already exists")
        jobs.append(job)
        self.save(jobs)

    def remove(self, name: str) -> CronJob:
        jobs = self.load()
        for idx, job in enumerate(jobs):
            if job.name == name:
                removed = jobs.pop(idx)
                self.save(jobs)
                return removed
        raise UnknownJobError(name)

    def get(self, name: str) -> CronJob:
        for job in self.load():
            if job.name == name:
                return job
        raise UnknownJobError(name)
