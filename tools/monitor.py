#!/usr/bin/env python3
"""Watch the intercom live: one line per relevant Home Assistant event and one
per finished Assist pipeline run (which device, what was heard, errors).

  python tools/monitor.py            print to the terminal
  python tools/monitor.py --log FILE also append to FILE

Stop with Ctrl-C. Read-only: subscribes to events and polls the pipeline debug
list every 2 s. Runs that started before the monitor are ignored.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import sys

import aiohttp

from ha import BASE, TOKEN, ws

WATCH = ("assist_satellite.", "event.home_assistant_voice", "script.intercom", "automation.intercom",
         "input_text.intercom")
LOG = None
if "--log" in sys.argv:
    LOG = open(sys.argv[sys.argv.index("--log") + 1], "a")


def emit(line: str) -> None:
    line = f"{datetime.datetime.now():%H:%M:%S} {line}"
    print(line, flush=True)
    if LOG:
        LOG.write(line + "\n"); LOG.flush()


async def event_stream() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        async with s.ws_connect(BASE + "/api/websocket", heartbeat=30, receive_timeout=None) as w:
            await w.receive_json()
            await w.send_json({"type": "auth", "access_token": TOKEN})
            assert (await w.receive_json())["type"] == "auth_ok"
            for i, et in enumerate(("state_changed", "automation_triggered", "call_service"), start=1):
                await w.send_json({"id": i, "type": "subscribe_events", "event_type": et})
            emit("monitor connected")
            async for msg in w:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                m = json.loads(msg.data)
                if m.get("type") != "event":
                    continue
                ev = m["event"]; d = ev["data"]; et = ev["event_type"]
                if et == "state_changed":
                    eid = d["entity_id"]
                    if eid.startswith(WATCH):
                        ns = d.get("new_state") or {}
                        extra = f" event_type={ns.get('attributes', {}).get('event_type')}" if eid.startswith("event.") else ""
                        emit(f"STATE {eid} -> {ns.get('state')!r}{extra}")
                elif et == "automation_triggered":
                    emit(f"AUTOMATION {d.get('name')!r} triggered")
                elif et == "call_service" and d.get("domain") in ("assist_satellite", "script", "logbook"):
                    emit(f"SERVICE {d['domain']}.{d['service']} {json.dumps(d.get('service_data') or {})[:300]}")


async def pipeline_poll() -> None:
    seen: dict[str, set] = {}
    pending: dict[tuple, str] = {}
    while True:
        try:
            pipelines = (await ws([{"type": "assist_pipeline/pipeline/list"}]))[0]["result"]["pipelines"]
            for p in pipelines:
                pid = p["id"]
                runs = (await ws([{"type": "assist_pipeline/pipeline_debug/list", "pipeline_id": pid}]))[0]["result"]["pipeline_runs"]
                runs = [r["pipeline_run_id"] if isinstance(r, dict) else r for r in runs]
                if pid not in seen:
                    seen[pid] = set(runs)
                    continue
                for rid in runs:
                    if rid not in seen[pid]:
                        seen[pid].add(rid)
                        pending[(pid, rid)] = p["name"]
            for (pid, rid), name in list(pending.items()):
                events = (await ws([{"type": "assist_pipeline/pipeline_debug/get", "pipeline_id": pid,
                                     "pipeline_run_id": rid}]))[0]["result"].get("events", [])
                types = [e["type"] for e in events]
                if "run-end" not in types and "error" not in types:
                    continue
                parts = [f"PIPELINE {name!r}"]
                for e in events:
                    t, data = e["type"], e.get("data") or {}
                    if t == "run-start":
                        parts.append(f"from={data.get('satellite_id') or data.get('device_id')}")
                    elif t == "stt-end":
                        parts.append(f"heard={data.get('stt_output', {}).get('text')!r}")
                    elif t == "intent-end":
                        speech = ((data.get("intent_output") or {}).get("response") or {}).get("speech", {}).get("plain", {}).get("speech")
                        parts.append(f"response={speech!r}")
                    elif t == "error":
                        parts.append(f"ERROR {data.get('code')}: {data.get('message')}")
                emit(" | ".join(parts))
                del pending[(pid, rid)]
        except Exception as ex:  # keep watching through transient failures
            emit(f"poll error: {ex!r}")
        await asyncio.sleep(2)


async def main() -> None:
    while True:
        try:
            await asyncio.gather(event_stream(), pipeline_poll())
        except Exception as ex:
            emit(f"stream error: {ex!r}; reconnecting")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
