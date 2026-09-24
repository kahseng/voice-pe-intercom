#!/usr/bin/env python3
"""Push the intercom configuration to Home Assistant through its API.

  python tools/push_config.py --devices devices.yaml [--dashboard [--hide-from-overview]]

  - creates the helpers from homeassistant/packages/voice_intercom.yaml that do
    not exist yet (input_text, input_boolean, timer); turns "push to phones" on
    when it is first created
  - replaces the script and the automations (scripts.yaml / automations.yaml,
    editable in the UI afterwards)
  - sets input_text.intercom_phones from "phones" in devices.yaml ("all", the
    default, or a list)
  - fills input_text.intercom_blocked_words with a default list if it is empty
  - records the Home Assistant administrators in input_text.intercom_admins
    (only their phones get the Mute button, and only they can mute)
  - with --dashboard, renders and saves two dashboards for the devices in
    devices.yaml (see devices.example.yaml): "Intercom" for everyone (Send box
    and last message) and "Intercom settings" for administrators only
  - with --hide-from-overview as well, hides the intercom's helpers, scripts,
    automations and timer and the Voice PE controls without an entity category
    from the auto-generated Overview dashboard

--devices is optional without --dashboard. Safe to re-run.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

from ha import rest, run, ws

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "homeassistant" / "packages" / "voice_intercom.yaml"
# Filled into input_text.intercom_blocked_words the first time only; edit it in
# the UI afterwards (Intercom settings). A trailing * also matches endings.
DEFAULT_BLOCKED_WORDS = (
    "fuck*, motherfuck*, shit*, bullshit*, bitch*, asshole*, ass, arse, bastard*, cunt*, dick, dicks, dickhead*, cock, "
    "cocks, penis*, piss*, crap*, damn*, goddamn*, whore*, slut*, twat*, wank*, prick*, bollock*"
)

FAMILY_PATH = "voice-intercom"            # everyone: send a message, see the last one
SETTINGS_PATH = "voice-intercom-settings"  # administrators only: everything else

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

RECENT_MESSAGES = (
    "{% set ns = namespace(lines=[]) %}"
    "{% for i in range(1, 11) %}"
    "{% set e = states('input_text.intercom_history_' ~ i) %}"
    "{% if e.count('|') >= 2 %}"
    "{% set p = e.split('|', 2) %}{% set t = as_datetime(p[0]) %}"
    "{% set when = (t | as_local).strftime('%H:%M') if (t | as_local).date() == now().date() "
    "else (t | as_local).strftime('%a %H:%M') %}"
    "{% set ns.lines = ns.lines + ['**' ~ when ~ '** \u00b7 ' ~ p[1] ~ '<br>' ~ p[2]] %}"
    "{% endif %}{% endfor %}"
    "{{ ns.lines | join('\n\n') if ns.lines else 'No messages yet.' }}"
)

SEND_SECTION = {"type": "grid", "column_span": 2, "cards": [
    {"type": "heading", "heading": "Send a message", "icon": "mdi:send"},
    {"type": "entities", "entities": [
        {"entity": "input_text.intercom_compose", "name": "Message"},
        {"type": "button", "name": "Announce on every speaker (or press Return)", "icon": "mdi:bullhorn",
         "action_name": "Send",
         "tap_action": {"action": "perform-action", "perform_action": "script.intercom_send_typed"}}]},
    {"type": "markdown", "title": "Recent messages", "content": RECENT_MESSAGES}]}


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
            {"entity": f"number.{pre}_wake_led_brightness", "name": "Wake LED brightness"},
            {"entity": f"select.home_assistant_voice_{sfx}_wake_word", "name": "Wake word"},
            {"entity": f"switch.home_assistant_voice_{sfx}_mute", "name": "Microphone mute"}]}]}


def render_family() -> dict:
    """What every household member sees: the Send box and the last message."""
    return {"title": "Intercom", "views": [{"title": "Intercom", "path": "intercom", "type": "sections",
                                            "max_columns": 2, "sections": [SEND_SECTION]}]}


def render_settings(devices: list[dict]) -> dict:
    """Administrators only: devices, status, sound graph, transcripts, tuning notes."""
    n = max(2, min(4, len(devices)))
    sections = [SEND_SECTION] + [device_section(d) for d in devices]
    sections.append({"type": "grid", "column_span": 2, "cards": [
        {"type": "heading", "heading": "Status", "icon": "mdi:bullhorn"},
        {"type": "entities", "entities": [
            {"entity": "input_boolean.intercom_push_to_phones", "name": "Push to phones"},
            {"entity": "input_text.intercom_phones", "name": "Phones"},
            {"entity": "input_text.intercom_admins", "name": "Administrators (user ids)"},
            {"entity": "input_text.intercom_blocked_words", "name": "Blocked words (shown as ****)"},
            {"entity": "timer.intercom_shout_mute", "name": "Shouts muted from a phone"},
            {"entity": "automation.intercom_broadcast_by_voice", "name": "Intercom automation"},
            {"entity": "automation.intercom_phone_notification_actions", "name": "Phone buttons automation"},
            {"entity": "automation.intercom_send_typed_message_when_the_box_is_committed", "name": "Send on Return"}]}]})
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
    return {"title": "Intercom settings", "views": [{"title": "Intercom settings", "path": "settings", "type": "sections",
                                                     "max_columns": n, "sections": sections}]}


async def save_dashboard(url_path: str, title: str, icon: str, require_admin: bool, cfg: dict) -> None:
    dashboards = (await ws([{"type": "lovelace/dashboards/list"}]))[0]["result"]
    existing = next((d for d in dashboards if d.get("url_path") == url_path), None)
    if existing is None:
        r = (await ws([{"type": "lovelace/dashboards/create", "url_path": url_path, "title": title, "icon": icon,
                        "mode": "storage", "require_admin": require_admin, "show_in_sidebar": True}]))[0]
        print(f"dashboard {url_path}:", "created" if r["success"] else r.get("error"))
    elif existing.get("require_admin") != require_admin or existing.get("title") != title:
        r = (await ws([{"type": "lovelace/dashboards/update", "dashboard_id": existing["id"], "title": title,
                        "require_admin": require_admin}]))[0]
        print(f"dashboard {url_path}: access updated" if r["success"] else r.get("error"))
    r = (await ws([{"type": "lovelace/config/save", "url_path": url_path, "config": cfg}]))[0]
    print(f"dashboard {url_path} config:", "saved" if r["success"] else r.get("error"),
          "(administrators only)" if require_admin else "(everyone)")


async def hide_from_overview(devices: list[dict]) -> None:
    """Hide the intercom's helpers, scripts, automations and timer, and the Voice PE
    controls without an entity category, from the auto-generated Overview. Hidden
    entities keep working and still show on the dashboards above."""
    ents = (await ws([{"type": "config/entity_registry/list"}]))[0]["result"]
    sat_devices = {e["device_id"] for e in ents
                   if e["entity_id"] in {f"assist_satellite.home_assistant_voice_{d['suffix']}_assist_satellite" for d in devices}}
    targets = [e for e in ents if not e.get("hidden_by") and (
        (e["entity_id"].split(".")[0] in ("input_text", "input_boolean", "timer", "script", "automation")
         and "intercom" in e["entity_id"])
        or (e.get("device_id") in sat_devices and not e.get("entity_category")))]
    for e in targets:
        r = (await ws([{"type": "config/entity_registry/update", "entity_id": e["entity_id"], "hidden_by": "user"}]))[0]
        print(f"hidden from Overview: {e['entity_id']}" if r["success"] else f"hide failed {e['entity_id']}: {r.get('error')}")
    if not targets:
        print("hidden from Overview: nothing new")


async def main() -> None:
    pkg = yaml.safe_load(PACKAGE.read_text())
    status, states = await rest("GET", "/api/states")
    existing = {s["entity_id"] for s in states}

    devices_cfg = {}
    if "--devices" in sys.argv:
        devices_cfg = yaml.safe_load(pathlib.Path(sys.argv[sys.argv.index("--devices") + 1]).read_text()) or {}

    cmds, names = [], []
    for domain in ("input_text", "input_boolean", "timer"):
        for object_id, cfg in (pkg.get(domain) or {}).items():
            if f"{domain}.{object_id}" not in existing:
                cmd = {"type": f"{domain}/create", "name": cfg["name"], "icon": cfg.get("icon")}
                if domain == "input_text":
                    cmd["max"] = cfg.get("max", 255)
                if domain == "timer":
                    cmd["duration"] = cfg.get("duration", "00:30:00")
                    cmd["restore"] = cfg.get("restore", False)
                cmds.append(cmd)
                names.append(f"{domain}.{object_id}")
    for name, r in zip(names, await ws(cmds) if cmds else []):
        if not r["success"]:
            print(f"helper {name}:", r.get("error"))
            continue
        # Home Assistant derives the entity id from the display name; give it
        # the id the package, scripts and dashboard refer to.
        domain = name.split(".")[0]
        got = f"{domain}.{r['result']['id']}"
        if got != name:
            rr = (await ws([{"type": "config/entity_registry/update", "entity_id": got, "new_entity_id": name}]))[0]
            print(f"helper {name}: created as {got}, renamed" if rr["success"] else f"helper {name}: rename failed {rr.get('error')}")
        else:
            print(f"helper {name}: created")
    if not cmds:
        print("helpers: already present")
    if "input_boolean.intercom_push_to_phones" in names:
        await rest("POST", "/api/services/input_boolean/turn_on", {"entity_id": "input_boolean.intercom_push_to_phones"})
        print("push to phones: turned on")

    for object_id, cfg in (pkg.get("script") or {}).items():
        status, body = await rest("POST", f"/api/config/script/config/{object_id}", cfg)
        print(f"script {object_id}: {status} {body}")

    for auto in pkg.get("automation") or []:
        auto = dict(auto)
        auto_id = auto.pop("id")
        status, body = await rest("POST", f"/api/config/automation/config/{auto_id}", auto)
        print(f"automation {auto_id}: {status} {body}")

    if "phones" in devices_cfg:
        phones = devices_cfg["phones"]
        value = "all" if phones in (None, "all") else ", ".join(phones)
        status, _ = await rest("POST", "/api/services/input_text/set_value",
                               {"entity_id": "input_text.intercom_phones", "value": value})
        print(f"phones: {value or '(none)'} ({status})")

    words = (await rest("GET", "/api/states/input_text.intercom_blocked_words"))[1]
    if isinstance(words, dict) and words.get("state") in ("", "unknown", None):
        status, _ = await rest("POST", "/api/services/input_text/set_value",
                               {"entity_id": "input_text.intercom_blocked_words", "value": DEFAULT_BLOCKED_WORDS})
        print(f"blocked words: default list set ({status})")

    users = (await ws([{"type": "config/auth/list"}]))[0]["result"]
    admins = [u["id"] for u in users if not u.get("system_generated") and u.get("is_active")
              and (u.get("is_owner") or "system-admin" in (u.get("group_ids") or []))]
    status, _ = await rest("POST", "/api/services/input_text/set_value",
                           {"entity_id": "input_text.intercom_admins", "value": ",".join(admins)})
    print(f"administrators: {len(admins)} ({status})")

    if "--dashboard" in sys.argv:
        if "devices" not in devices_cfg:
            sys.exit("--dashboard needs --devices <devices.yaml> (see devices.example.yaml)")
        devices = devices_cfg["devices"]
        await save_dashboard(FAMILY_PATH, "Intercom", "mdi:bullhorn", False, render_family())
        await save_dashboard(SETTINGS_PATH, "Intercom settings", "mdi:tune", True, render_settings(devices))
        if "--hide-from-overview" in sys.argv:
            await hide_from_overview(devices)

if __name__ == "__main__":
    run(main())
