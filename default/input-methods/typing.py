"""Native menu selectors for keyboard layouts and active input methods."""

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

import configure as setup


def controller(method):
  return json.loads(setup.run(setup.CONTROLLER[:1] + ["--json=short"] + setup.CONTROLLER[1:] + [method]))["data"][0]


def keyboard_catalog():
  catalog = {}
  for layout, label, languages, variants in controller("AvailableKeyboardLayouts"):
    catalog[layout] = label
    for variant, description, languages in variants:
      catalog[f"{layout}:{variant}"] = description
  return catalog


def keyboard_selection():
  layouts = json.loads(setup.run(["hyprctl", "-j", "getoption", "input:kb_layout"]))["str"].split(",")
  variants = json.loads(setup.run(["hyprctl", "-j", "getoption", "input:kb_variant"]))["str"].split(",")
  return [layout + (":" + variants[index] if index < len(variants) and variants[index] else "")
          for index, layout in enumerate(layouts)]


def choose(title, catalog, selected):
  # Current entries stay first, in switching order, followed by the catalog.
  keys = list(dict.fromkeys(selected + sorted(catalog, key=lambda key: catalog[key])))
  labels = {key: catalog.get(key, key) for key in keys}
  counts = Counter(labels.values())
  rows = {key: "\t" + labels[key] + (f" ({key})" if counts[labels[key]] > 1 else "") for key in keys}
  args = ["omarchy-menu-select", title] + list(rows.values())
  args += ["--", "--multiple", "--width", "620", "--maxheight", "650"]
  for key in selected:
    args += ["--selected", rows[key][1:]]
  result = subprocess.run(args, text=True, capture_output=True)
  if result.returncode == 1:
    return None
  if result.returncode:
    raise RuntimeError(result.stderr.strip() or "The selection menu could not open")
  values = json.loads(result.stdout)
  valid = {row[1:]: key for key, row in rows.items()}
  if not isinstance(values, list) or any(not isinstance(value, str) or value not in valid for value in values):
    raise ValueError("The menu returned an invalid selection")
  return list(dict.fromkeys(valid[value] for value in values))


def input_catalog(items):
  available = {item[0]: item[1] for item in setup.available_methods()}
  catalog = {key: preset["label"] for key, preset in setup.PRESETS.items() if key != "none"}
  for name, layout in items:
    if not name.startswith("keyboard-"):
      catalog.setdefault(name, available.get(name, name))
  return catalog, available


def input_items(items, selected):
  keyboard = [item for item in items if item[0].startswith("keyboard-")]
  if not keyboard:
    raise RuntimeError("The current input group has no keyboard input")
  existing = dict(items)
  return keyboard + [[name, existing.get(name, "")] for name in selected]


def set_inputs(group, layout, before, after):
  if before == after:
    return
  try:
    # Removing the active engine must return typing to the keyboard.
    setup.run(["fcitx5-remote", "-c"])
    setup.live_set(group, layout, after)
    if setup.live_group() != (group, layout, after):
      raise RuntimeError("Fcitx did not retain the input selection")
  except BaseException:
    setup.live_set(group, layout, before)
    raise


def configure_inputs():
  group, layout, items = setup.live_group()
  catalog, available = input_catalog(items)
  current = [name for name, override in items if not name.startswith("keyboard-")]
  selected = choose("Input Methods", catalog, current)
  if selected is None:
    return False
  missing = [catalog[name] for name in selected if name not in available]
  if missing:
    raise RuntimeError("Restart input after updating Omarchy to use: " + ", ".join(missing))
  # Abort rather than overwrite a group changed while the picker was open.
  if setup.live_group() != (group, layout, items):
    raise RuntimeError("Input settings changed while the menu was open. Please try again.")
  config_home = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
  for name in selected:
    if name in setup.PRESETS:
      setup.font_default(config_home, name)
  set_inputs(group, layout, items, input_items(items, selected))
  return True


def keyboard_values(selected):
  if not selected:
    raise ValueError("Select at least one keyboard layout")
  parts = [value.split(":", 1) for value in selected]
  # Preserve the installer's Latin-leading rule for desktop keybindings.
  if parts[0][0] in setup.NON_LATIN:
    parts.insert(0, ["us"])
  layouts = ",".join(part[0] for part in parts)
  variants = ",".join(part[1] if len(part) > 1 else "" for part in parts)
  return layouts, variants


def save_keyboard(selected):
  layouts, variants = keyboard_values(selected)
  config_home = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
  path = config_home / "omarchy/keyboard-layouts"
  original = setup.read(path) if path.exists() else None
  group, old_layout, before = setup.live_group()
  group_changed = False
  try:
    setup.atomic_write(path, f"XKBLAYOUT={layouts}\nXKBVARIANT={variants}\n")
    setup.run(["hyprctl", "reload"])
    errors = setup.run(["hyprctl", "configerrors"])
    if errors:
      raise RuntimeError(errors)
    expected = [part[0] + (":" + part[1] if len(part) > 1 and part[1] else "")
                for part in [value.split(":", 1) for value in selected]]
    if selected[0].split(":", 1)[0] in setup.NON_LATIN:
      expected.insert(0, "us")
    if keyboard_selection() != expected:
      raise RuntimeError("Your personal keyboard layout override takes precedence over this selection")
    first_layout = layouts.split(",")[0]
    first_variant = variants.split(",")[0]
    group_layout = first_layout + ("-" + first_variant if first_variant else "")
    primary = next((item[0] for item in before if item[0].startswith("keyboard-")), None)
    if primary is None:
      raise RuntimeError("The current input group has no keyboard input")
    keyboard = "keyboard-" + group_layout
    after = [[keyboard, ""]] + [item for item in before if item[0] not in (primary, keyboard)]
    group_changed = True
    setup.live_set(group, group_layout, after)
    if setup.live_group() != (group, group_layout, after):
      raise RuntimeError("Fcitx did not retain the keyboard selection")
  except BaseException:
    if original is None:
      path.resolve().unlink(missing_ok=True)
    else:
      setup.atomic_write(path, original)
    setup.run(["hyprctl", "reload"])
    if group_changed:
      setup.live_set(group, old_layout, before)
    raise


def configure_keyboard():
  current = keyboard_selection()
  catalog = keyboard_catalog()
  selected = choose("Keyboard Layouts", catalog, current)
  if selected is None:
    return False
  if keyboard_selection() != current:
    raise RuntimeError("Keyboard settings changed while the menu was open. Please try again.")
  save_keyboard(selected)
  return True


def main():
  kind = sys.argv[1] if len(sys.argv) == 2 else ""
  if kind not in ("input", "keyboard"):
    raise ValueError("Choose keyboard or input setup")
  title = "Input methods" if kind == "input" else "Keyboard layouts"
  try:
    applied = configure_inputs() if kind == "input" else configure_keyboard()
  except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
    subprocess.run(["omarchy-notification-send", title + " could not be updated", str(error)], check=True)
    raise SystemExit(1)
  if applied:
    subprocess.run(["omarchy-notification-send", title + " updated", "Use Super + I to switch inputs"], check=True)


if __name__ == "__main__":
  main()
