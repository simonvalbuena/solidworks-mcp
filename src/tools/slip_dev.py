"""Development hatch for the Slip fork: call (and hot-reload) fork functions without restarting
Claude Desktop, and run long COM jobs in the background (the device link times out after 60 s).

* fork_call(module, function, args_json, reload_modules, background)
    module: a module under tools/ (e.g. "slip_clone"); function: a module-level function name.
    args_json: JSON object of keyword arguments. reload_modules: JSON list of tools modules to
    importlib.reload first (in order) — picks up code deployed since the server started.
    background=true queues the job on the COM worker and returns a job id at once.
* fork_job(job_id): status / result of a background job (result once done).

Only functions of modules inside the tools package are reachable.
"""

from __future__ import annotations

import importlib
import itertools
import json
import logging
import time
import traceback
from concurrent.futures import Future

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

_JOBS: dict[str, dict] = {}
_IDS = itertools.count(1)


def _resolve(module: str, function: str, reload_modules):
    if not module.replace("_", "").isalnum():
        raise ToolError(f"bad module name {module!r}")
    for m in reload_modules or ():
        if not str(m).replace("_", "").isalnum():
            raise ToolError(f"bad module name {m!r}")
        importlib.reload(importlib.import_module(f"tools.{m}"))
    mod = importlib.import_module(f"tools.{module}")
    fn = getattr(mod, function, None)
    if fn is None or not callable(fn) or function.startswith("__"):
        raise ToolError(f"tools.{module}.{function} not found")
    return fn


def _queue(sw: SWConnection, fn, kwargs) -> Future:
    fut: Future = Future()
    with sw._queue_lock:                         # same path as SWConnection.execute
        sw._queue.append((fn, (), kwargs, fut))
    sw._queue_event.set()
    return fut


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def fork_call(module: str, function: str, args_json: str = "{}",
                        reload_modules: str = "[]", background: bool = False) -> str:
        """Call tools.<module>.<function>(**args) on the SolidWorks COM thread (dev hatch).
        reload_modules: JSON list of tools modules to hot-reload first. background=true returns
        {"job": id} immediately — poll with fork_job (for jobs longer than the 60 s link)."""
        fn = _resolve(module, function, json.loads(reload_modules or "[]"))
        kwargs = json.loads(args_json or "{}")
        if background:
            jid = f"J{next(_IDS)}"
            fut = _queue(sw, fn, kwargs)
            _JOBS[jid] = {"future": fut, "start": time.time(), "call": f"{module}.{function}"}
            return json.dumps({"job": jid, "status": "queued"})
        try:
            return json.dumps(await sw.execute(fn, **kwargs), ensure_ascii=False, default=str)
        except SWError as e:
            raise ToolError(f"{module}.{function} failed: {e}") from e
        except Exception as e:  # noqa: BLE001
            logger.exception("fork_call crashed")
            raise ToolError(f"{module}.{function} failed: {type(e).__name__}: {e}\n"
                            f"{traceback.format_exc()[-1500:]}") from e

    @mcp.tool()
    async def fork_job(job_id: str) -> str:
        """Status of a background fork_call job: running (elapsed s) or its result / error."""
        job = _JOBS.get(job_id)
        if job is None:
            raise ToolError(f"no job {job_id!r}")
        fut: Future = job["future"]
        out = {"job": job_id, "call": job["call"], "elapsed_s": round(time.time() - job["start"], 1)}
        if not fut.done():
            out["status"] = "running"
            return json.dumps(out)
        try:
            out["status"] = "done"
            out["result"] = fut.result()
        except Exception as e:  # noqa: BLE001
            out["status"] = "error"
            out["error"] = f"{type(e).__name__}: {e}"
            out["trace"] = "".join(traceback.format_exception(e))[-1500:]
        return json.dumps(out, ensure_ascii=False, default=str)
