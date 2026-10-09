import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ["OMARCHY_PATH"])
sys.path.insert(0, str(ROOT / "default/input-methods"))
spec = importlib.util.spec_from_file_location("typing_setup", ROOT / "default/input-methods/typing.py")
typing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(typing)


class TypingSetupTest(unittest.TestCase):
  def test_variants_and_installer_binding_safety(self):
    self.assertEqual(typing.keyboard_values(["us:intl", "fr"]), ("us,fr", "intl,"))
    self.assertEqual(typing.keyboard_values(["ru:phonetic"]), ("us,ru", ",phonetic"))
    with self.assertRaises(ValueError):
      typing.keyboard_values([])

  def test_keyboard_catalog_includes_variants(self):
    with patch.object(typing, "controller", return_value=[["us", "English (US)", ["en"], [["intl", "English (US, international)", ["en"]]]]]):
      self.assertEqual(typing.keyboard_catalog(), {"us": "English (US)", "us:intl": "English (US, international)"})

  def test_input_deselection_keeps_keyboard_and_selected_overrides(self):
    items = [["keyboard-us", ""], ["mozc", "jp"], ["hangul", ""], ["custom", "de"]]
    self.assertEqual(typing.input_items(items, ["custom", "mozc"]), [["keyboard-us", ""], ["custom", "de"], ["mozc", "jp"]])
    self.assertEqual(typing.input_items(items, []), [["keyboard-us", ""]])

  def test_cancellation_does_not_change_input_settings(self):
    with patch.object(typing.setup, "live_group", return_value=("Default", "us", [["keyboard-us", ""]])), patch.object(typing, "input_catalog", return_value=({"mozc": "Japanese"}, {"mozc": "Japanese"})), patch.object(typing, "choose", return_value=None), patch.object(typing.setup, "live_set") as setter:
      self.assertFalse(typing.configure_inputs())
      setter.assert_not_called()

  def test_failed_input_save_restores_the_original_group(self):
    before = [["keyboard-us", ""], ["mozc", "jp"]]
    after = [["keyboard-us", ""]]
    with patch.object(typing.setup, "run"), patch.object(typing.setup, "live_set") as setter, patch.object(typing.setup, "live_group", return_value=("Default", "us", before)):
      with self.assertRaises(RuntimeError):
        typing.set_inputs("Default", "us", before, after)
      self.assertEqual(setter.call_args_list[-1].args, ("Default", "us", before))

  def test_picker_preselects_current_entries_and_returns_empty_selection(self):
    result = subprocess.CompletedProcess([], 0, stdout="[]")
    with patch.object(typing.subprocess, "run", return_value=result) as process:
      self.assertEqual(typing.choose("Inputs", {"mozc": "Japanese", "hangul": "Korean"}, ["hangul"]), [])
      args = process.call_args.args[0]
      self.assertEqual(args[2], "\tKorean")
      self.assertIn("--multiple", args)
      self.assertEqual(args[-2:], ["--selected", "Korean"])

  def test_picker_rejects_unknown_returned_values(self):
    with patch.object(typing.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout='["unknown"]')):
      with self.assertRaises(ValueError):
        typing.choose("Inputs", {"mozc": "Japanese"}, [])

  def test_keyboard_override_failure_preserves_symlink_and_restores_settings(self):
    with tempfile.TemporaryDirectory() as temporary:
      config = Path(temporary)
      target = config / "actual-layouts"
      target.write_text("XKBLAYOUT=de\n")
      target.chmod(0o600)
      path = config / "omarchy/keyboard-layouts"
      path.parent.mkdir()
      path.symlink_to(target)
      before = ("Default", "us", [["keyboard-us", ""], ["mozc", "jp"]])
      with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}), patch.object(typing.setup, "live_group", return_value=before), patch.object(typing.setup, "run", return_value=""), patch.object(typing, "keyboard_selection", return_value=["de"]), patch.object(typing.setup, "live_set") as setter:
        with self.assertRaisesRegex(RuntimeError, "override"):
          typing.save_keyboard(["us", "fr"])
      self.assertTrue(path.is_symlink())
      self.assertEqual(target.read_text(), "XKBLAYOUT=de\n")
      self.assertEqual(target.stat().st_mode & 0o777, 0o600)
      setter.assert_not_called()

  def test_keyboard_save_aligns_fcitx_and_preserves_language_overrides(self):
    with tempfile.TemporaryDirectory() as temporary:
      before = ("Default", "us", [["keyboard-us", ""], ["mozc", "jp"]])
      after = ("Default", "fr", [["keyboard-fr", ""], ["mozc", "jp"]])
      with patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}), patch.object(typing.setup, "live_group", side_effect=[before, after]), patch.object(typing.setup, "run", return_value=""), patch.object(typing, "keyboard_selection", return_value=["fr", "us:intl"]), patch.object(typing.setup, "live_set") as setter:
        typing.save_keyboard(["fr", "us:intl"])
      self.assertEqual(setter.call_args.args, after)
      self.assertEqual((Path(temporary) / "omarchy/keyboard-layouts").read_text(), "XKBLAYOUT=fr,us\nXKBVARIANT=,intl\n")


if __name__ == "__main__":
  unittest.main()
