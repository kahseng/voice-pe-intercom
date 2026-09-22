#!/bin/sh
# Compile (and optionally flash over the air) one Voice PE config.
#   esphome/build.sh path/to/home-assistant-voice-xxxxxx.yaml              compile only
#   esphome/build.sh path/to/home-assistant-voice-xxxxxx.yaml <device-ip>  compile and upload
# Needs ESPHome on the PATH (see README, "Firmware").
set -e
[ -n "$1" ] && [ -f "$1" ] || { echo "usage: $0 <device.yaml> [device-ip]"; exit 1; }
if [ -n "$2" ]; then
  esphome run "$1" --device "$2" --no-logs
else
  esphome compile "$1"
fi
