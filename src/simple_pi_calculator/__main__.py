"""``python -m simple_pi_calculator`` entry point (DESIGN.md §5.1, §7.1).

The GUI bootstrap lives in :mod:`simple_pi_calculator.app`; it is imported lazily so that this
module stays importable without Qt.
"""

from __future__ import annotations

import sys


def _run() -> int:
    from simple_pi_calculator.app import main  # noqa: PLC0415 - lazy import (Qt)

    result = main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(_run())
