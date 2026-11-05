"""Strategy interface. A strategy emits PM orders at two points of each session:

* ``morning(i)``  — before the open, reading only data stamped <= the prior close (flows,
  generator orders); these execute today.
* ``evening(i)``  — after the close of session i (rebalance announcements, off-cycle changes,
  corporate-action exits); these execute on a later session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simp.domain.records import PmOrder
    from simp.runs.engine import Engine


class Strategy(Protocol):
    name: str
    version: str

    def bind(self, engine: Engine) -> None: ...

    def initial(self, i: int) -> list[PmOrder]: ...

    def morning(self, i: int) -> list[PmOrder]: ...

    def evening(self, i: int) -> list[PmOrder]: ...
