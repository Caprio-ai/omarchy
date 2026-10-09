"""Live Fcitx state for the existing keyboard layout widget."""

from collections import Counter
import json
import os
import re
import subprocess
import sys

from gi.repository import Gio, GLib


BUS = "org.fcitx.Fcitx5"
INTERFACE = "org.fcitx.Fcitx.Controller1"


def call(bus, method, args=None):
  # Reading the bar must neither activate a stopped service nor wait indefinitely.
  return bus.call_sync(BUS, "/controller", INTERFACE, method, args, None,
                       Gio.DBusCallFlags.NO_AUTO_START, 1000, None).unpack()


def snapshot(bus):
  group = call(bus, "CurrentInputMethodGroup")[0]
  methods = list(dict.fromkeys(item[0] for item in
                              call(bus, "InputMethodGroupInfo", GLib.Variant("(s)", (group,)))[1]))
  info = call(bus, "CurrentInputMethodInfo")
  return {"methods": methods, "current": info[0], "name": info[1], "label": info[4], "language": info[5]}


def cycle(bus, state=None):
  state = state if state is not None else snapshot(bus)
  methods = state["methods"]
  if len(methods) > 1 and state["current"] in methods:
    following = methods[(methods.index(state["current"]) + 1) % len(methods)]
    call(bus, "SetCurrentIM", GLib.Variant("(s)", (following,)))


def layout_switches(keyboards):
  typed = [keyboard for keyboard in keyboards if not re.match(
    r"^(hl-virtual-keyboard|power-button|sleep-button|lid-switch|video-bus)", keyboard.get("name", ""))]
  typed = [item for item in typed if len(item.get("layout", "").split(",")) > 1]
  if not typed:
    return []
  # Consumer controls also carry the seat layout. Its frequency keeps an
  # individually configured keyboard from taking over the desktop shortcut.
  counts = Counter(item.get("layout", "") for item in keyboards)
  layout = max((item["layout"] for item in typed), key=lambda layout: counts[layout])
  keyboard = max((item for item in typed if item["layout"] == layout),
                 key=lambda item: item.get("active_layout_index", 0))
  layout = keyboard.get("layout", "")
  count = len(layout.split(","))
  if count < 2:
    return []
  following = (keyboard.get("active_layout_index", 0) + 1) % count
  return [["hyprctl", "switchxkblayout", item["name"], str(following)]
          for item in keyboards if item.get("layout") == layout and item.get("name")]


def cycle_input():
  # Fcitx keyboard entries count as input methods too. With just one method,
  # switch the compositor layouts even if the bar widget has been removed.
  try:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = snapshot(bus)
  except GLib.Error:
    state = {}
  if len(state.get("methods", [])) > 1:
    cycle(bus, state)
  else:
    devices = subprocess.run(["hyprctl", "-j", "devices"], check=True, text=True, capture_output=True)
    for command in layout_switches(json.loads(devices.stdout).get("keyboards", [])):
      subprocess.run(command, check=True, capture_output=True)


class Indicator:
  def __init__(self, bus):
    self.bus = bus
    self.pending = ""
    self.last = ""
    self.entries = {}

  def select_next(self):
    state = snapshot(self.bus)
    methods = state["methods"]
    if len(methods) < 2:
      return
    current = self.pending or state["current"] or self.last or methods[0]
    if current not in methods:
      current = methods[0]
    self.pending = methods[(methods.index(current) + 1) % len(methods)]
    self.refresh()

  def refresh(self):
    state = snapshot(self.bus)
    methods = state["methods"]
    if self.pending not in methods:
      self.pending = ""
    if self.pending and state["current"]:
      # Before the first application gains focus Fcitx has no input context.
      # Retain the click until it has one, without changing startup defaults.
      call(self.bus, "SetCurrentIM", GLib.Variant("(s)", (self.pending,)))
      state = snapshot(self.bus)
      if state["current"] == self.pending:
        self.pending = ""
    if state["current"]:
      self.last = state["current"]
    else:
      chosen = self.pending or (self.last if self.last in methods else "") or next(iter(methods), "")
      if chosen not in self.entries:
        self.entries = {entry[0]: entry for entry in call(self.bus, "AvailableInputMethods")[0]}
      entry = self.entries.get(chosen)
      if entry:
        state.update(current=entry[0], name=entry[1], label=entry[4], language=entry[5])
    return state


def watch(bus):
  previous = None
  indicator = Indicator(bus)
  loop = GLib.MainLoop()
  buffer = ""
  timer = 0

  def refresh():
    nonlocal previous
    try:
      state = indicator.refresh()
    except GLib.Error:
      indicator.pending = ""
      indicator.last = ""
      state = {"methods": [], "current": "", "name": "", "language": ""}
    line = json.dumps(state, ensure_ascii=False)
    if line != previous:
      print(line, flush=True)
      previous = line

  def poll():
    nonlocal timer
    refresh()
    timer = GLib.timeout_add(50 if indicator.pending else 500, poll)
    return GLib.SOURCE_REMOVE

  def command(source, condition):
    nonlocal buffer, timer
    data = os.read(sys.stdin.fileno(), 4096)
    if not data:
      loop.quit()
      return GLib.SOURCE_REMOVE
    buffer += data.decode()
    while "\n" in buffer:
      line, buffer = buffer.split("\n", 1)
      if line == "cycle":
        try:
          indicator.select_next()
        except GLib.Error:
          indicator.pending = ""
        refresh()
    GLib.source_remove(timer)
    timer = GLib.timeout_add(50 if indicator.pending else 500, poll)
    return GLib.SOURCE_CONTINUE

  # Fcitx's controller has no current-method-changed signal. Keep one bus
  # connection open instead of spawning a command on every poll. Polling also
  # follows per-application input contexts and recovers after service restarts.
  GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, command)
  poll()
  loop.run()


if __name__ == "__main__":
  try:
    if sys.argv[1:] == ["cycle"]:
      cycle_input()
    else:
      watch(Gio.bus_get_sync(Gio.BusType.SESSION, None))
  except (GLib.Error, OSError, ValueError, subprocess.CalledProcessError) as error:
    raise SystemExit(str(error))
