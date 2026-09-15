"""Thread-pool helpers and BLAS thread policy for the computation core (DESIGN.md §3.9).

Policy
------
* ``workers`` = number of Python worker threads; 0 / ``None`` = auto = ``os.cpu_count()``.
* numpy releases the GIL inside BLAS/LAPACK calls and ufunc loops, so frequency chunks of one PWR
  net and different PWR nets run truly in parallel in threads.
* The engine's hot loops are many *small* dense operations (batched K×K solves/SVDs, P×M GEMMs).
  A multithreaded BLAS inside such calls only adds thread hand-off overhead (measured: batched
  120×120 complex SVD 0.55 s with 1 BLAS thread vs 1.46 s with 2 on a 2-core machine) and
  oversubscribes the cores already used by the worker threads. :func:`blas_limited` therefore pins
  BLAS to one thread for the duration of a computation when the optional ``threadpoolctl``
  package is importable; without it the BLAS keeps its environment default
  (``OPENBLAS_NUM_THREADS`` / ``OMP_NUM_THREADS``).
* Results never depend on the number of workers: every chunk computes exactly the same
  per-frequency arithmetic; only the scheduling differs.

Qt-free, numpy only.
"""

from __future__ import annotations

import math
import os
import threading
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence, TypeVar

__all__ = [
    "resolve_workers",
    "blas_limited",
    "plan_chunks",
    "run_chunks",
    "CHUNK_TARGET_BYTES",
    "PARALLEL_MIN_BYTES",
]

T = TypeVar("T")

#: Target working-set size of one frequency chunk (fits the L2/L3 cache of desktop CPUs well)
CHUNK_TARGET_BYTES = 8 * 1024 * 1024
#: Below this total work size a stage runs serially (thread hand-off would dominate)
PARALLEL_MIN_BYTES = 2 * 1024 * 1024
#: Upper bound for auto-detected workers (diminishing returns, memory per chunk)
MAX_AUTO_WORKERS = 32


def resolve_workers(workers: int | None) -> int:
    """0 / None / negative → auto (``os.cpu_count()``, capped at 32); otherwise the value (≥ 1)."""
    if workers is None or int(workers) <= 0:
        return max(1, min(MAX_AUTO_WORKERS, os.cpu_count() or 1))
    return max(1, int(workers))


_blas_lock = threading.Lock()
_blas_depth = 0
_blas_ctx = None


@contextmanager
def blas_limited(threads: int = 1) -> Iterator[bool]:
    """Limit BLAS/OpenMP threads while the block runs (re-entrant, process-wide).

    Yields ``True`` when a limit is in effect (``threadpoolctl`` available), else ``False``.
    Only the outermost ``with`` applies/restores the limit, so nested and concurrent engine calls
    from several threads share one limit.
    """
    global _blas_depth, _blas_ctx
    with _blas_lock:
        if _blas_depth == 0:
            try:
                from threadpoolctl import threadpool_limits  # optional dependency
                _blas_ctx = threadpool_limits(limits=max(1, int(threads)), user_api="blas")
            except Exception:  # noqa: BLE001 - not installed or unsupported BLAS: no limit
                _blas_ctx = None
        _blas_depth += 1
        active = _blas_ctx is not None
    try:
        yield active
    finally:
        with _blas_lock:
            _blas_depth -= 1
            if _blas_depth == 0 and _blas_ctx is not None:
                try:
                    _blas_ctx.restore_original_limits()
                except Exception:  # noqa: BLE001
                    pass
                _blas_ctx = None


def plan_chunks(n_items: int, bytes_per_item: float, workers: int,
                target_bytes: int = CHUNK_TARGET_BYTES,
                min_parallel_bytes: int = PARALLEL_MIN_BYTES) -> list[slice]:
    """Split ``range(n_items)`` into contiguous slices.

    Each slice holds ≈ ``target_bytes`` of working set; when parallel work is worthwhile
    (``workers > 1`` and the total exceeds ``min_parallel_bytes``) there are at least
    ``2·workers`` slices so that uneven per-item cost still balances across threads.
    """
    n = int(n_items)
    if n <= 0:
        return []
    per = max(1.0, float(bytes_per_item))
    total = per * n
    n_chunks = max(1, int(math.ceil(total / target_bytes)))
    if workers > 1 and total >= min_parallel_bytes:
        n_chunks = max(n_chunks, 2 * workers)
    n_chunks = min(n, n_chunks)
    size = int(math.ceil(n / n_chunks))
    return [slice(s, min(n, s + size)) for s in range(0, n, size)]


class _Cancelled(Exception):
    pass


def run_chunks(fn: Callable[[slice], T], chunks: Sequence[slice], workers: int,
               cancel: Callable[[], bool] | None = None,
               on_done: Callable[[int, int], None] | None = None,
               cancelled_exc: type[BaseException] = RuntimeError) -> list[T]:
    """Run ``fn(chunk)`` for every chunk; returns the results in chunk order.

    Serial when ``workers <= 1`` or there is a single chunk; otherwise a private
    :class:`ThreadPoolExecutor` (never shared with the caller, so nested use cannot deadlock).
    ``cancel`` is polled before each chunk starts; when it returns true the remaining chunks are
    skipped and ``cancelled_exc`` is raised. The first exception raised by a chunk is re-raised
    after running chunks finished (pending chunks are cancelled). ``on_done(done, total)`` is called
    from the *calling* thread after each completed chunk.
    """
    total = len(chunks)
    if total == 0:
        return []
    if workers <= 1 or total == 1:
        out = []
        for i, ch in enumerate(chunks):
            if cancel is not None and cancel():
                raise cancelled_exc("computation cancelled")
            out.append(fn(ch))
            if on_done is not None:
                on_done(i + 1, total)
        return out

    stop = threading.Event()

    def task(ch: slice) -> T:
        if stop.is_set():
            raise _Cancelled()
        if cancel is not None and cancel():
            stop.set()
            raise cancelled_exc("computation cancelled")
        return fn(ch)

    results: list[T | None] = [None] * total
    with ThreadPoolExecutor(max_workers=min(workers, total),
                            thread_name_prefix="spical-chunk") as pool:
        futures: dict[Future, int] = {pool.submit(task, ch): i for i, ch in enumerate(chunks)}
        pending = set(futures)
        done_count = 0
        first_exc: BaseException | None = None
        while pending:
            done, pending = wait(pending, return_when=FIRST_EXCEPTION)
            for fut in done:
                if fut.cancelled():
                    continue
                exc = fut.exception()
                if exc is None:
                    results[futures[fut]] = fut.result()
                    done_count += 1
                    if on_done is not None and first_exc is None:
                        on_done(done_count, total)
                elif not isinstance(exc, _Cancelled):
                    stop.set()
                    # a real error wins over a cancellation observed by another chunk
                    if first_exc is None or (isinstance(first_exc, cancelled_exc)
                                             and not isinstance(exc, cancelled_exc)):
                        first_exc = exc
                elif first_exc is None:  # skipped because another chunk stopped the run
                    first_exc = cancelled_exc("computation cancelled")
            if first_exc is not None:
                for fut in pending:
                    fut.cancel()
        if first_exc is not None:
            raise first_exc
    return results  # type: ignore[return-value]
