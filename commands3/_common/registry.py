"""Process-wide default scheduler.

A robot program uses one variant (e.g. ``commands3.custom.asyncawait``). Importing a variant
registers its Scheduler class; the first registered class creates the default scheduler that
``Mechanism``/``Trigger``/buttons fall back to when no scheduler is given explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scheduler import SchedulerBase

_default_cls: type | None = None
_default: "SchedulerBase | None" = None


def register_variant(cls: type) -> None:
    global _default_cls
    if _default_cls is None:
        _default_cls = cls


def get_default_scheduler() -> "SchedulerBase":
    global _default
    if _default is None:
        if _default_cls is None:
            raise RuntimeError(
                "No commands3 variant imported; import e.g. commands3.custom.asyncawait"
            )
        _default = _default_cls._create()  # type: ignore[attr-defined]
    assert _default is not None
    return _default


def set_default_scheduler(scheduler: "SchedulerBase | None") -> None:
    """Replace (or with ``None``, reset) the process-wide default scheduler."""
    global _default, _default_cls
    _default = scheduler
    if scheduler is not None:
        _default_cls = type(scheduler)
