#!/usr/bin/env python3
"""Push the intercom configuration to Home Assistant through its API.

  python tools/push_config.py              helpers, script and automation from
                                           homeassistant/packages/voice_intercom.yaml
  python tools/push_config.py --dashboard --devices devices.yaml
                                           also render and save the "voice-intercom"
                                           dashboard for the devices listed in that file
                                           (see devices.example.yaml)

The script lands in scripts.yaml and the automation in automations.yaml, both
editable in the UI afterwards. Safe to re-run: existing helpers are left alone,
the script and automation are replaced.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

from ha import rest, run, ws

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "homeassistant" / "packages" / "voice_intercom.yaml"
DASHBOARD_PATH = "voice-intercom"

TUNING_NOTES = (
    "1. Stand where you normally use the device.\n"
    "2. Talk at normal volume for a few seconds, then shout **hey** two or three times.\n"
    "3. Read the graph: the tallest normal-talking bump and the shout peaks.\n"
    "4. Set the slider about **6 dB above** normal talking. Closer to 0 is *harder* to trigger, "
    "closer to -100 is *easier*.\n\n"
    "The meter shows the loudest moment of each second. Typical values: quiet room -84, normal "
    "talking peaks -52, shouts -45 to -32. False triggers → slider toward 0. Missed shouts → slider toward -100.\n\n"
    "The slider is a floor. During sustained conversation nearby the device raises its *effective threshold* "
    "to the ambient speech level plus the *adaptive margin*, and lets it decay 3 dB per minute once it is quiet."
)


def device_section(d: dict) -> dict:
    sfx, pre = d["suffix"], d["entity_prefix"]
    return {"type": "grid", "column_span": 1, "cards": [
        {"type": "heading", "heading": d["title"], "icon": d.get("icon", "mdi:microphone")},
        {"type": "entities", "state_color": True, "entities": [
            {"entity": f"assist_satellite.home_assistant_voice_{sfx}_assist_satellite", "name": "State"},
            {"entity": f"switch.{pre}_shout_to_talk", "name": "Shout to talk"},
            {"entity": f"number.{pre}_intercom_shout_threshold", "name": "Shout threshold (floor)"},
            {"entity": f"number.{pre}_intercom_adaptive_margin", "name": "Adaptive margin"},
            {"entity": f"sensor.{pre}_intercom_effective_threshold", "name": "Effective threshold"},
            {"entity": f"sensor.{pre}_intercom_ambient_level", "name": "Ambient speech level"},
            {"entity": f"sensor.{pre}_intercom_sound_level", "name": "Sound level now"},
            {"entity": f"select.home_assistant_voice_{sfx}_wake_word", "name": "Wake word"},
            {"entity": f"switch.home_assistant_voice_{sfx}_mute", "name": "Microphone mute"}]}]}


def render_dashboard(devices: list[dict]) -> dict:
    """The Intercom dashboard: devices side by side, then the sound graph, transcripts and notes."""
    n = max(2, min(4, len(devices)))
    sections = [device_section(d) for d in devices]
    sections.append({"type": "grid", "column_span": 2, "cards": [
        {"type": "heading", "heading": "Status", "icon": "mdi:bullhorn"},
        {"type": "entities", "entities": [
            {"entity": "input_text.intercom_last_message", "name": "Last message"},
            {"entity": "input_text.intercom_last_sender", "name": "From"},
            {"entity": "automation.intercom_broadcast_by_voice", "name": "Intercom automation"}]}]})
    sections.append({"type": "grid", "column_span": n, "cards": [
        {"type": "heading", "heading": "Sound level, last 30 minutes (dB, 0 = loudest)", "icon": "mdi:waveform"},
        {"type": "history-graph", "hours_to_show": 0.5, "refresh_interval": 5, "grid_options": {"columns": "full"},
         "entities": [{"entity": f"sensor.{d['entity_prefix']}_intercom_sound_level", "name": d["title"]} for d in devices]
                     + [{"entity": f"sensor.{d['entity_prefix']}_intercom_effective_threshold", "name": f"{d['title']} threshold"} for d in devices]}]})
    sections.append({"type": "grid", "column_span": 2, "cards": [
        {"type": "heading", "heading": "Transcripts, last 2 days", "icon": "mdi:text-box-outline"},
        {"type": "logbook", "hours_to_show": 48, "grid_options": {"columns": "full"},
         "target": {"entity_id": ["input_boolean.intercom_transcripts"]}}]})
    sections.append({"type": "grid", "column_span": 2, "cards": [
        {"type": "heading", "heading": "Tuning shout-to-talk", "icon": "mdi:tune"},
        {"type": "markdown", "grid_options": {"columns": "full"}, "content": TUNING_NOTES}]})
    return {"title": "Intercom", "views": [{"title": "Intercom", "path": "intercom", "type": "sections",
                                            "max_columns": n, "sections": sections}]}


async def main() -> None:
    pkg = yaml.safe_load(PACKAGE.read_text())
    status, states = await rest("GET", "/api/states")
    existing = {s["entity_id"] for s in states}

    cmds = []
    for domain in ("input_text", "input_boolean"):
        for object_id, cfg in (pkg.get(domain) or {}).items():
            if f"{domain}.{object_id}" not in existing:
                cmd = {"type": f"{domain}/create", "name": cfg["name"], "icon": cfg.get("icon")}
                if domain == "input_text":
                    cmd["max"] = cfg.get("max", 255)
                cmds.append(cmd)
    for r in await ws(cmds) if cmds else []:
        print("helper:", "created" if r["success"] else r.get("error"))
    if not cmds:
        print("helpers: already present")

    for object_id, cfg in (pkg.get("script") or {}).items():
        status, body = await rest("POST", f"/api/config/script/config/{object_id}", cfg)
        print(f"script {object_id}: {status} {body}")

    for auto in pkg.get("automation") or []:
        auto = dict(auto)
        auto_id = auto.pop("id")
        status, body = await rest("POST", f"/api/config/automation/config/{auto_id}", auto)
        print(f"automation {auto_id}: {status} {body}")

    if "--dashboard" in sys.argv:
        if "--devices" not in sys.argv:
            sys.exit("--dashboard needs --devices <devices.yaml> (see devices.example.yaml)")
        devices = yaml.safe_load(pathlib.Path(sys.argv[sys.argv.index("--devices") + 1]).read_text())["devices"]
        cfg = render_dashboard(devices)
        dashboards = (await ws([{"type": "lovelace/dashboards/list"}]))[0]["result"]
        if not any(d.get("url_path") == DASHBOARD_PATH for d in dashboards):
            r = (await ws([{"type": "lovelace/dashboards/create", "url_path": DASHBOARD_PATH,
                            "title": cfg.get("title", "Intercom"), "icon": "mdi:bullhorn",
                            "mode": "storage", "require_admin": False, "show_in_sidebar": True}]))[0]
            print("dashboard:", "created" if r["success"] else r.get("error"))
        r = (await ws([{"type": "lovelace/config/save", "url_path": DASHBOARD_PATH, "config": cfg}]))[0]
        print("dashboard config:", "saved" if r["success"] else r.get("error"))


if __name__ == "__main__":
    run(main())
