"""Background execution. Default: in-process thread pool (no Redis needed). `JOB_BACKEND=inline` runs synchronously
(tests). The job function itself is idempotent, so swapping this module for Celery/RQ is a drop-in change:
register `run_with_retry` as the task body. (Celery wiring is not included in this build.)"""
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from . import stt

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ns-job")


def run_with_retry(fn, *args, attempts=3, base_delay=2.0, **kw):
    for i in range(attempts):
        try:
            return fn(*args, **kw)
        except stt.ProviderError:
            if i == attempts - 1:
                return
            time.sleep(base_delay * (2 ** i))


def submit(fn, *args, **kw):
    if get_settings().job_backend == "inline":
        return run_with_retry(fn, *args, base_delay=0, **kw)
    return _pool.submit(run_with_retry, fn, *args, **kw)
