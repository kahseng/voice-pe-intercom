"""Minimal Home Assistant client shared by the tools in this folder.

Configuration:
  HA_URL    base URL of Home Assistant, default http://homeassistant.local:8123
  HA_TOKEN  long-lived access token; if unset, read from the macOS Keychain
            item with service name "ha-token" (create it with
            security add-generic-password -a homeassistant -s ha-token -w "$(pbpaste)")
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import aiohttp

BASE = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")


def _token() -> str:
    if tok := os.environ.get("HA_TOKEN"):
        return tok.strip()
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", "ha-token", "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    sys.exit("No token: set HA_TOKEN or store one in the Keychain under service 'ha-token'.")


TOKEN = _token()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


async def rest(method: str, path: str, body=None, timeout: float | None = 300):
    """REST call. Returns (status, parsed JSON or text)."""
    async with aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.request(method, BASE + path, json=body) as r:
            text = await r.text()
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text


async def ws(commands: list[dict]) -> list[dict]:
    """Run websocket commands in order on one connection; returns the result messages."""
    out = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        async with s.ws_connect(BASE + "/api/websocket", heartbeat=30, receive_timeout=None) as w:
            await w.receive_json()  # auth_required
            await w.send_json({"type": "auth", "access_token": TOKEN})
            msg = await w.receive_json()
            if msg.get("type") != "auth_ok":
                sys.exit(f"websocket auth failed: {msg}")
            for i, cmd in enumerate(commands, start=1):
                await w.send_json({"id": i, **cmd})
                while True:
                    msg = await w.receive_json()
                    if msg.get("id") == i and msg.get("type") == "result":
                        out.append(msg)
                        break
    return out


def run(coro):
    return asyncio.run(coro)
