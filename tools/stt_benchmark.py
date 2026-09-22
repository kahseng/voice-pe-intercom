#!/usr/bin/env python3
"""Time the speech-to-text stage of an Assist pipeline with a WAV clip.

  python tools/stt_benchmark.py clip.wav [--pipeline NAME] [--runs N] [--full]

The clip must be 16 kHz, mono, 16-bit PCM. On a Mac you can make one with:
  say -v Samantha -o clip.aiff "tell everyone dinner is ready"
  afconvert -f WAVE -d LEI16@16000 -c 1 clip.aiff clip.wav

By default only the speech-to-text stage runs, so nothing is spoken anywhere.
--full also runs the intent stage, which on the intercom pipeline BROADCASTS
the transcript to every satellite: your devices will speak. Use deliberately.
"""
from __future__ import annotations

import asyncio
import sys
import time
import wave

import aiohttp

from ha import BASE, TOKEN, ws


async def pipeline_id(name: str) -> str:
    pipelines = (await ws([{"type": "assist_pipeline/pipeline/list"}]))[0]["result"]["pipelines"]
    for p in pipelines:
        if p["name"] == name:
            return p["id"]
    sys.exit(f"no pipeline named {name!r}; have: {[p['name'] for p in pipelines]}")


async def one_run(pid: str, pcm: bytes, end_stage: str):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        async with s.ws_connect(BASE + "/api/websocket", receive_timeout=None) as w:
            await w.receive_json()
            await w.send_json({"type": "auth", "access_token": TOKEN})
            assert (await w.receive_json())["type"] == "auth_ok"
            await w.send_json({"id": 1, "type": "assist_pipeline/run", "start_stage": "stt", "end_stage": end_stage,
                               "input": {"sample_rate": 16000}, "pipeline": pid, "timeout": 60})
            handler = None; sent_at = None; text = None; error = None
            while True:
                m = await w.receive_json()
                if m.get("type") == "result" and not m.get("success"):
                    return None, m.get("error"), 0.0
                if m.get("type") != "event":
                    continue
                et, data = m["event"]["type"], m["event"].get("data") or {}
                if et == "run-start":
                    handler = data["runner_data"]["stt_binary_handler_id"]
                elif et == "stt-start" and sent_at is None:
                    hb = bytes([handler])
                    for i in range(0, len(pcm), 3200):          # 100 ms chunks, roughly real time
                        await w.send_bytes(hb + pcm[i:i + 3200]); await asyncio.sleep(0.05)
                    await w.send_bytes(hb + b"\x00" * 3200 * 5)  # half a second of silence so VAD ends
                    await w.send_bytes(hb)                       # end of audio
                    sent_at = time.time()
                elif et == "stt-end":
                    text = data["stt_output"]["text"]; took = time.time() - sent_at
                elif et == "error":
                    error = data
                elif et == "run-end":
                    return text, error, (took if text else 0.0)


async def main() -> None:
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        sys.exit(__doc__)
    wav = args[0]
    name = args[args.index("--pipeline") + 1] if "--pipeline" in args else "Intercom"
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 3
    end_stage = "intent" if "--full" in args else "stt"
    with wave.open(wav) as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (16000, 1, 2), "clip must be 16 kHz mono 16-bit"
        pcm = f.readframes(f.getnframes())
    pid = await pipeline_id(name)
    for _ in range(runs):
        text, error, took = await one_run(pid, pcm, end_stage)
        print(f"  heard {text!r} in {took:.2f} s" if text else f"  error: {error}")


if __name__ == "__main__":
    asyncio.run(main())
