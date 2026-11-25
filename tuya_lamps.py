#!/usr/bin/env python3
"""
Tuya Lamps - control Tuya Wi-Fi smart lamps locally over the LAN.

This single file contains three layers:

1. The *core*: loading the device configuration and talking to the lamps
   with TinyTuya (no Qt involved).
2. The *CLI*: ``tuya-lamps list`` and ``tuya-lamps set ...``, which print
   JSON. The KDE Plasma widget uses these commands.
3. The *GUI*: a PySide6 window with one card per lamp.

Run ``tuya-lamps --help`` for usage.

License: GPL-3.0-or-later (see the LICENSE file).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

__version__ = "1.0.0"

APP_NAME = "Tuya Lamps"

# Tuya product categories that are lights. "dj" = light, "dd" = strip,
# "fwd" = ambient light, "xdd" = ceiling light, "dc" = string lights,
# "tgq" = dimmer, "fsd" = ceiling fan light.
LIGHT_CATEGORIES = {"dj", "dd", "fwd", "xdd", "dc", "tgq", "fsd"}

# Network settings for every lamp connection.
SOCKET_TIMEOUT = 3      # seconds to wait for an answer
SOCKET_RETRY_LIMIT = 1  # how often to retry a failed connection

UNREACHABLE_MESSAGE = "unreachable (is the lamp powered and on the same network?)"


# ===========================================================================
# 1. Core: configuration
# ===========================================================================

class ConfigError(Exception):
    """Raised when devices.json is missing or cannot be read."""


@dataclass
class LampConfig:
    """Everything we need to know to connect to one lamp."""

    id: str
    name: str
    key: str
    category: str = ""
    ip: str = ""
    version: str = ""


def config_dir() -> Path:
    """Return the folder that holds devices.json (and snapshot.json)."""
    override = os.environ.get("TUYA_LAMPS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "tuya-lamps"


def _read_json(path: Path):
    """Read a JSON file and turn any problem into a ConfigError."""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error


def load_lamp_configs(folder: Path | None = None) -> list[LampConfig]:
    """
    Load the lamps from devices.json, merged with snapshot.json.

    - devices.json is written by ``python -m tinytuya wizard``.
    - snapshot.json (written by a network scan) often has newer IP
      addresses and protocol versions, so those win.
    - Devices without a local key cannot be controlled and are skipped.
    - Only light categories are kept, unless that leaves nothing.
    """
    folder = folder or config_dir()
    devices_file = folder / "devices.json"
    if not devices_file.exists():
        raise ConfigError(f"{devices_file} not found")

    devices = _read_json(devices_file)
    if not isinstance(devices, list):
        raise ConfigError(f"{devices_file} does not contain a list of devices")

    # Build a lookup table {device id: snapshot entry}.
    snapshot = {}
    snapshot_file = folder / "snapshot.json"
    if snapshot_file.exists():
        data = _read_json(snapshot_file)
        entries = data.get("devices", []) if isinstance(data, dict) else []
        snapshot = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}

    lamps = []
    for entry in devices:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("key"):
            continue  # Without an id and a local key we cannot talk to it.
        extra = snapshot.get(entry["id"], {})
        lamps.append(LampConfig(
            id=str(entry["id"]),
            name=str(entry.get("name") or entry["id"]),
            key=str(entry["key"]),
            category=str(entry.get("category") or ""),
            ip=str(extra.get("ip") or entry.get("ip") or ""),
            version=str(extra.get("ver") or entry.get("version") or ""),
        ))

    lights = [lamp for lamp in lamps if lamp.category in LIGHT_CATEGORIES]
    result = lights or lamps  # Fall back to all devices if no lights found.
    return sorted(result, key=lambda lamp: lamp.name.lower())


# ===========================================================================
# 1. Core: talking to a lamp
# ===========================================================================

class LampError(Exception):
    """A lamp did not answer or returned an error."""


def friendly_error(message: str) -> str:
    """Turn a technical TinyTuya error into something a human understands."""
    text = str(message).lower()
    if any(word in text for word in ("timeout", "unreachable", "unable to connect",
                                     "unable to find", "not configured", "network error",
                                     "timed out", "connection refused", "no route")):
        return UNREACHABLE_MESSAGE
    if "key or version" in text:
        return "wrong local key or protocol version (re-run the TinyTuya wizard)"
    if "not supported" in text:
        return "this lamp does not support that"
    return str(message)


def check(result):
    """
    TinyTuya returns a dict with an "Error" key when something fails.
    Raise a LampError in that case, otherwise return the result unchanged.
    """
    if isinstance(result, dict) and "Error" in result:
        raise LampError(friendly_error(result["Error"]))
    return result


def make_device(lamp: LampConfig, persistent: bool = False):
    """
    Create a TinyTuya BulbDevice for a lamp.

    Note: with address "Auto" TinyTuya scans the network *inside the
    constructor*, which can take several seconds. Never call this from
    the GUI thread.
    """
    import tinytuya  # Imported here so "--help" stays fast.

    try:
        version = float(lamp.version) if lamp.version else None
    except ValueError:
        version = None

    try:
        if lamp.ip and version:
            device = tinytuya.BulbDevice(lamp.id, lamp.ip, lamp.key, version=version)
        else:
            device = tinytuya.BulbDevice(lamp.id, "Auto", lamp.key)
    except Exception as error:  # e.g. "Unable to find device on network"
        raise LampError(friendly_error(error)) from error

    device.set_socketTimeout(SOCKET_TIMEOUT)
    device.set_socketRetryLimit(SOCKET_RETRY_LIMIT)
    device.set_socketPersistent(persistent)
    return device


def read_state(device) -> dict:
    """
    Read the current state of a lamp.

    Returns a dict with: is_on, mode, brightness (0-100), colourtemp (0-100,
    0 = warmest), hue (0-359), sat (0-100), val (0-100), has_colour and
    has_colourtemp. Raises LampError on failure.
    """
    try:
        state = check(device.state())
        has_colour = device.bulb_has_capability("colour")
        has_colourtemp = device.bulb_has_capability("colourtemp")

        result = {
            "is_on": bool(state.get("is_on")),
            "mode": state.get("mode") or "white",
            "brightness": None,
            "colourtemp": None,
            "hue": None,
            "sat": None,
            "val": None,
            "has_colour": has_colour,
            "has_colourtemp": has_colourtemp,
        }

        if state.get("brightness") is not None:
            result["brightness"] = round(device.get_brightness_percentage(state=state))
        if has_colourtemp and state.get("colourtemp") is not None:
            result["colourtemp"] = round(device.get_colourtemp_percentage(state=state))
        if has_colour and state.get("colour"):
            hsv = check(device.colour_hsv(state=state))
            hue, sat, val = hsv
            result["hue"] = round(hue * 360) % 360
            result["sat"] = round(sat * 100)
            result["val"] = round(val * 100)
            # In colour mode the brightness is the "value" of the colour.
            if result["mode"] == "colour":
                result["brightness"] = result["val"]
        return result
    except LampError:
        raise
    except Exception as error:  # TinyTuya raises RuntimeError/ValueError too.
        raise LampError(friendly_error(error)) from error


def send_power(device, on: bool):
    """Switch a lamp on or off."""
    check(device.turn_on() if on else device.turn_off())


def send_colour(device, hue: float, sat: float, brightness: float):
    """Set a colour. hue 0-359, sat 0-100, brightness 0-100. Turns the lamp on."""
    check(device.set_hsv(hue / 360.0, sat / 100.0, max(brightness, 1) / 100.0))


def send_white(device, brightness: float, colourtemp: float):
    """Set white light. brightness 0-100, colourtemp 0 (warm) - 100 (cool)."""
    check(device.set_white_percentage(max(int(brightness), 1), int(colourtemp)))


def send_brightness(device, brightness: float):
    """Change only the brightness and keep the current mode."""
    check(device.set_brightness_percentage(max(int(brightness), 1)))


# ===========================================================================
# 2. CLI
# ===========================================================================

def print_json(data):
    """Print data as pretty JSON on stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def query_lamp(lamp: LampConfig) -> dict:
    """Read one lamp for "tuya-lamps list". Never raises."""
    row = {
        "id": lamp.id, "name": lamp.name, "online": False, "is_on": None,
        "mode": None, "brightness": None, "colourtemp": None, "hue": None,
        "sat": None, "val": None, "has_colour": True, "has_colourtemp": True,
        "error": None,
    }
    device = None
    try:
        device = make_device(lamp)
        row.update(read_state(device))
        row["online"] = True
    except LampError as error:
        row["error"] = str(error)
    finally:
        if device is not None:
            device.close()
    return row


def find_lamps(lamps: list[LampConfig], target: str) -> list[LampConfig]:
    """Find lamps by id, by (case-insensitive) name, or "all"."""
    if target.lower() == "all":
        return lamps
    by_id = [lamp for lamp in lamps if lamp.id == target]
    if by_id:
        return by_id
    return [lamp for lamp in lamps if lamp.name.lower() == target.lower()]


def apply_settings(lamp: LampConfig, args) -> dict:
    """Apply the options of "tuya-lamps set" to one lamp. Never raises."""
    result = {"id": lamp.id, "name": lamp.name, "ok": False, "error": None}
    device = None
    try:
        device = make_device(lamp)
        if args.off or args.brightness == 0:
            send_power(device, False)
        elif args.colour is not None:
            hue, sat = args.colour
            if args.brightness is not None:
                brightness = args.brightness
            else:
                # Keep the current brightness if we can read it.
                state = read_state(device)
                brightness = state["val"] if state["mode"] == "colour" else state["brightness"]
                brightness = brightness or 100
            send_colour(device, hue, sat, brightness)
        elif args.white is not None:
            if args.brightness is not None:
                brightness = args.brightness
            else:
                brightness = read_state(device)["brightness"] or 100
            send_white(device, brightness, args.white)
        elif args.brightness is not None:
            send_brightness(device, args.brightness)
        else:
            # Only --on, or no options at all: switch on.
            send_power(device, True)
        result["ok"] = True
    except LampError as error:
        result["error"] = str(error)
    except Exception as error:  # Unexpected TinyTuya behaviour.
        result["error"] = friendly_error(error)
    finally:
        if device is not None:
            device.close()
    return result


def int_in_range(low: int, high: int):
    """Create an argparse type that accepts whole numbers from low to high."""
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{text}' is not a whole number") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{value} is out of range ({low}-{high})")
        return value
    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuya-lamps",
        description="Control Tuya Wi-Fi lamps locally. Without a command the GUI starts.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("gui", help="open the desktop app (default)")
    commands.add_parser("list", help="print all lamps and their state as JSON")

    set_cmd = commands.add_parser(
        "set", help="change one lamp, or all lamps",
        description="Change a lamp. Without options the lamp is switched on. "
                    "Brightness 0 switches the lamp off.",
    )
    set_cmd.add_argument("target", help="lamp id, lamp name (case-insensitive) or 'all'")
    power = set_cmd.add_mutually_exclusive_group()
    power.add_argument("--on", action="store_true", help="switch on")
    power.add_argument("--off", action="store_true", help="switch off")
    set_cmd.add_argument("--brightness", type=int_in_range(0, 100), metavar="0-100",
                         help="brightness in percent")
    light = set_cmd.add_mutually_exclusive_group()
    light.add_argument("--colour", "--color", dest="colour", nargs=2, metavar=("HUE", "SAT"),
                       help="colour mode: hue 0-359 and saturation 0-100")
    light.add_argument("--white", type=int_in_range(0, 100), metavar="TEMP",
                       help="white mode: colour temperature 0 (warm) to 100 (cool)")
    return parser


def validate_set_args(parser: argparse.ArgumentParser, args):
    """Checks that argparse cannot do on its own."""
    if args.colour is not None:
        hue_text, sat_text = args.colour
        try:
            args.colour = (int_in_range(0, 359)(hue_text), int_in_range(0, 100)(sat_text))
        except argparse.ArgumentTypeError as error:
            parser.error(f"argument --colour: {error}")
    if args.off and (args.colour is not None or args.white is not None
                     or args.brightness is not None):
        parser.error("--off cannot be combined with --brightness, --colour or --white")


def cmd_list() -> int:
    try:
        lamps = load_lamp_configs()
    except ConfigError as error:
        print_json({"error": str(error), "code": "no_config"})
        return 1
    # Query all lamps at the same time, so one offline lamp doesn't slow
    # down the others. map() keeps the original order.
    with ThreadPoolExecutor(max_workers=max(len(lamps), 1)) as pool:
        rows = list(pool.map(query_lamp, lamps))
    print_json(rows)
    return 0


def cmd_set(args) -> int:
    try:
        lamps = load_lamp_configs()
    except ConfigError as error:
        print_json({"ok": False, "error": str(error), "code": "no_config"})
        return 1

    targets = find_lamps(lamps, args.target)
    if not targets:
        print_json({"id": None, "name": args.target, "ok": False,
                    "error": f"no lamp with id or name '{args.target}'"})
        return 1

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda lamp: apply_settings(lamp, args), targets))

    print_json(results if args.target.lower() == "all" else results[0])
    return 0 if all(result["ok"] for result in results) else 1


# ===========================================================================
# 3. GUI
# ===========================================================================
# PySide6 is imported here (not at the top) so the CLI starts quickly and
# also works without PySide6 installed.

def run_gui() -> int:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import (
        QApplication, QButtonGroup, QCheckBox, QColorDialog, QFrame, QGridLayout,
        QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea,
        QSizePolicy, QSlider, QVBoxLayout, QWidget,
    )

    # --- Background worker ---------------------------------------------------

    class WorkerSignals(QObject):
        """Signals a worker thread uses to report back to the GUI thread.

        Qt delivers signals emitted from another thread safely to the GUI
        thread (as a "queued connection"), so the GUI never blocks.
        """
        state_read = Signal(dict)  # a fresh lamp state
        succeeded = Signal()       # a command worked
        failed = Signal(str)       # something went wrong (friendly text)

    class LampWorker:
        """
        One background thread per lamp, with a small command queue.

        The queue is a dict keyed by command type ("read", "power",
        "light"). A new command of the same type *replaces* the older one
        that is still waiting. So while you drag a slider only the latest
        value is sent, instead of a long line of outdated values.
        """

        def __init__(self, lamp: LampConfig):
            self.lamp = lamp
            self.signals = WorkerSignals()
            self.device = None
            self.pending: dict[str, object] = {}
            self.condition = threading.Condition()
            self.stopped = False
            # A daemon thread does not keep the app alive when the window
            # closes, even if it is still waiting for a slow lamp.
            self.thread = threading.Thread(target=self._run, daemon=True,
                                           name=f"lamp-{lamp.id}")
            self.thread.start()

        def submit(self, kind: str, payload=None):
            """Queue a command. Replaces a waiting command of the same kind."""
            with self.condition:
                self.pending.pop(kind, None)   # remove the old one...
                self.pending[kind] = payload   # ...and add the new one at the end
                self.condition.notify()

        def stop(self):
            with self.condition:
                self.stopped = True
                self.condition.notify()

        def _run(self):
            while True:
                with self.condition:
                    while not self.pending and not self.stopped:
                        self.condition.wait()
                    if self.stopped:
                        break
                    # Take the oldest waiting command (dicts keep order).
                    kind = next(iter(self.pending))
                    payload = self.pending.pop(kind)
                self._execute(kind, payload)
            if self.device is not None:
                self.device.close()

        def _execute(self, kind: str, payload):
            try:
                if self.device is None:
                    self.device = make_device(self.lamp, persistent=True)
                if kind == "read":
                    state = read_state(self.device)
                    self._emit(self.signals.state_read, state)
                    return
                if kind == "power":
                    send_power(self.device, payload)
                elif kind == "light":
                    if payload["mode"] == "colour":
                        send_colour(self.device, payload["hue"], payload["sat"],
                                    payload["brightness"])
                    else:
                        send_white(self.device, payload["brightness"], payload["colourtemp"])
                self._emit(self.signals.succeeded)
            except Exception as error:
                # Drop the connection; the next command will reconnect.
                if self.device is not None:
                    self.device.close()
                self.device = None
                message = str(error) if isinstance(error, LampError) else friendly_error(error)
                self._emit(self.signals.failed, message)

        def _emit(self, signal, *values):
            if not self.stopped:
                signal.emit(*values)

    # --- Small widgets -------------------------------------------------------

    def gradient_slider_style(stops: list[str]) -> str:
        """Style sheet for a slider whose groove shows a colour gradient."""
        step = 1 / (len(stops) - 1)
        stop_text = ", ".join(f"stop:{i * step:.3f} {colour}" for i, colour in enumerate(stops))
        return f"""
            QSlider::groove:horizontal {{
                height: 10px; border-radius: 5px;
                border: 1px solid palette(mid);
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stop_text});
            }}
            QSlider::handle:horizontal {{
                width: 14px; height: 14px; margin: -4px 0; border-radius: 8px;
                background: palette(button); border: 1px solid palette(window-text);
            }}
        """

    HUE_STOPS = ["#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"]
    WARM_COOL_STOPS = ["#ff9a3c", "#ffffff", "#aaccff"]
    BRIGHTNESS_STOPS = ["#202020", "#ffffff"]

    def make_slider(minimum: int, maximum: int, stops: list[str] | None = None) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if stops:
            slider.setStyleSheet(gradient_slider_style(stops))
        return slider

    def white_preview(colourtemp: float) -> QColor:
        """Approximate colour of white light: 0 = warm orange, 100 = cool blue."""
        warm, white, cool = QColor("#ff9a3c"), QColor("#ffffff"), QColor("#aaccff")
        if colourtemp <= 50:
            a, b, t = warm, white, colourtemp / 50
        else:
            a, b, t = white, cool, (colourtemp - 50) / 50
        return QColor(round(a.red() + (b.red() - a.red()) * t),
                      round(a.green() + (b.green() - a.green()) * t),
                      round(a.blue() + (b.blue() - a.blue()) * t))

    # --- One card per lamp ---------------------------------------------------

    class LampCard(QFrame):
        """The controls for one lamp."""

        DOT_COLOURS = {"unknown": "#888888", "online": "#3cb043", "offline": "#d9534f"}

        def __init__(self, lamp: LampConfig, window):
            super().__init__()
            self.lamp = lamp
            self.window = window
            self.worker = LampWorker(lamp)
            self.updating = False  # True while we copy a lamp state into the controls

            self.setObjectName("lampCard")
            self.setStyleSheet("""
                QFrame#lampCard {
                    background: palette(base);
                    border: 1px solid palette(mid);
                    border-radius: 8px;
                }
                QPushButton[mode="true"]:checked {
                    background: palette(highlight);
                    color: palette(highlighted-text);
                    border: 1px solid palette(highlight);
                    border-radius: 4px;
                    padding: 4px 16px;
                    min-width: 48px;
                    font-weight: bold;
                }
            """)

            # Header: status dot, name, On toggle.
            self.dot = QLabel()
            self.dot.setFixedSize(12, 12)
            self.name = QLabel(f"<b>{html.escape(lamp.name)}</b>")
            self.power = QCheckBox("On")

            header = QHBoxLayout()
            header.addWidget(self.dot)
            header.addWidget(self.name, 1)
            header.addWidget(self.power)

            # Mode buttons and colour swatch.
            self.colour_button = QPushButton("Colour")
            self.white_button = QPushButton("White")
            self.mode_group = QButtonGroup(self)
            for button in (self.colour_button, self.white_button):
                button.setCheckable(True)
                button.setProperty("mode", True)
                self.mode_group.addButton(button)
            self.white_button.setChecked(True)
            self.swatch = QPushButton()
            self.swatch.setFixedSize(48, 26)
            self.swatch.setToolTip("Pick an exact colour")

            modes = QHBoxLayout()
            modes.addWidget(self.colour_button)
            modes.addWidget(self.white_button)
            modes.addStretch(1)
            modes.addWidget(self.swatch)

            # Sliders.
            self.hue = make_slider(0, 359, HUE_STOPS)
            self.sat = make_slider(0, 100)
            self.temp = make_slider(0, 100, WARM_COOL_STOPS)
            self.brightness = make_slider(1, 100, BRIGHTNESS_STOPS)
            self.sat.setValue(100)
            self.temp.setValue(50)
            self.brightness.setValue(100)
            self.brightness_label = QLabel("100 %")
            self.brightness_label.setMinimumWidth(48)

            grid = QGridLayout()
            self.hue_label = QLabel("Hue")
            self.sat_label = QLabel("Saturation")
            self.temp_label = QLabel("Warm ↔ cool")
            grid.addWidget(self.hue_label, 0, 0)
            grid.addWidget(self.hue, 0, 1, 1, 2)
            grid.addWidget(self.sat_label, 1, 0)
            grid.addWidget(self.sat, 1, 1, 1, 2)
            grid.addWidget(self.temp_label, 2, 0)
            grid.addWidget(self.temp, 2, 1, 1, 2)
            grid.addWidget(QLabel("Brightness"), 3, 0)
            grid.addWidget(self.brightness, 3, 1)
            grid.addWidget(self.brightness_label, 3, 2)
            grid.setColumnStretch(1, 1)

            layout = QVBoxLayout(self)
            layout.addLayout(header)
            layout.addLayout(modes)
            layout.addLayout(grid)

            # Debounce: wait ~120 ms after the last slider movement before
            # sending. Combined with the worker queue this keeps traffic low.
            self.debounce = QTimer(self)
            self.debounce.setSingleShot(True)
            self.debounce.setInterval(120)
            self.debounce.timeout.connect(self.send_light)

            # Connect signals.
            self.power.toggled.connect(self.on_power_toggled)
            self.mode_group.buttonClicked.connect(self.on_mode_clicked)
            for slider in (self.hue, self.sat, self.temp, self.brightness):
                slider.valueChanged.connect(self.on_slider_changed)
            self.swatch.clicked.connect(self.pick_colour)
            self.worker.signals.state_read.connect(self.apply_state)
            self.worker.signals.succeeded.connect(lambda: self.set_status("online"))
            self.worker.signals.failed.connect(self.on_failed)

            self.set_status("unknown")
            self.set_capabilities(True, True)
            self.update_visibility()
            self.update_swatch()

        # -- Helpers --

        def is_colour_mode(self) -> bool:
            return self.colour_button.isChecked()

        def set_status(self, status: str):
            self.dot.setStyleSheet(
                f"background: {self.DOT_COLOURS[status]}; border-radius: 6px;")
            self.dot.setToolTip(status)

        def set_capabilities(self, has_colour: bool, has_colourtemp: bool):
            self.has_colour = has_colour
            self.has_colourtemp = has_colourtemp
            self.colour_button.setVisible(has_colour)
            self.white_button.setVisible(has_colour)  # no choice without colour
            self.swatch.setVisible(has_colour)
            if not has_colour:
                self.white_button.setChecked(True)

        def update_visibility(self):
            colour = self.is_colour_mode() and self.has_colour
            for widget in (self.hue, self.hue_label, self.sat, self.sat_label):
                widget.setVisible(colour)
            white = not colour and self.has_colourtemp
            self.temp.setVisible(white)
            self.temp_label.setVisible(white)

        def current_colour(self) -> QColor:
            if self.is_colour_mode():
                return QColor.fromHsv(self.hue.value(), round(self.sat.value() * 2.55),
                                      round(self.brightness.value() * 2.55))
            colour = white_preview(self.temp.value())
            factor = self.brightness.value() / 100
            return QColor(round(colour.red() * factor), round(colour.green() * factor),
                          round(colour.blue() * factor))

        def update_swatch(self):
            self.swatch.setStyleSheet(
                f"background: {self.current_colour().name()};"
                " border: 1px solid palette(window-text); border-radius: 4px;")
            self.brightness_label.setText(f"{self.brightness.value()} %")

        def mark_on(self):
            """Changing the light also switches the lamp on: show that."""
            if not self.power.isChecked():
                self.updating = True
                self.power.setChecked(True)
                self.updating = False

        # -- User actions --

        def on_power_toggled(self, checked: bool):
            if not self.updating:
                self.worker.submit("power", checked)

        def on_mode_clicked(self, _button):
            self.update_visibility()
            self.update_swatch()
            if not self.updating:
                self.mark_on()
                self.send_light()

        def on_slider_changed(self, _value):
            self.update_swatch()
            if not self.updating:
                self.mark_on()
                self.debounce.start()  # restarts the timer if already running

        def pick_colour(self):
            colour = QColorDialog.getColor(self.current_colour(), self, f"Colour for {self.lamp.name}")
            if not colour.isValid():
                return
            self.updating = True
            self.colour_button.setChecked(True)
            self.hue.setValue(max(colour.hsvHue(), 0))  # hue is -1 for grey
            self.sat.setValue(round(colour.hsvSaturationF() * 100))
            self.brightness.setValue(max(round(colour.valueF() * 100), 1))
            self.updating = False
            self.update_visibility()
            self.update_swatch()
            self.mark_on()
            self.send_light()

        def send_light(self):
            self.debounce.stop()
            if self.is_colour_mode():
                payload = {"mode": "colour", "hue": self.hue.value(), "sat": self.sat.value(),
                           "brightness": self.brightness.value()}
            else:
                payload = {"mode": "white", "brightness": self.brightness.value(),
                           "colourtemp": self.temp.value()}
            self.worker.submit("light", payload)

        def refresh(self):
            self.worker.submit("read")

        # -- Results from the worker --

        def apply_state(self, state: dict):
            """Copy a lamp state into the controls without sending commands."""
            self.updating = True
            try:
                self.set_status("online")
                self.set_capabilities(state["has_colour"], state["has_colourtemp"])
                self.power.setChecked(state["is_on"])
                if state["mode"] == "colour" and state["has_colour"]:
                    self.colour_button.setChecked(True)
                else:
                    self.white_button.setChecked(True)
                for slider, key in ((self.hue, "hue"), (self.sat, "sat"),
                                    (self.temp, "colourtemp"), (self.brightness, "brightness")):
                    if state.get(key) is not None:
                        slider.setValue(state[key])
            finally:
                self.updating = False
            self.update_visibility()
            self.update_swatch()

        def on_failed(self, message: str):
            self.set_status("offline")
            self.window.show_message(f"{self.lamp.name}: {message}")

    # --- Main window -------------------------------------------------------------

    class MainWindow(QMainWindow):
        def __init__(self, lamps: list[LampConfig]):
            super().__init__()
            self.setWindowTitle(APP_NAME)
            self.resize(460, 640)

            all_on = QPushButton("All on")
            all_off = QPushButton("All off")
            refresh = QPushButton("Refresh")
            all_on.clicked.connect(lambda: self.set_all(True))
            all_off.clicked.connect(lambda: self.set_all(False))
            refresh.clicked.connect(self.refresh_all)

            top = QHBoxLayout()
            top.addWidget(all_on)
            top.addWidget(all_off)
            top.addStretch(1)
            top.addWidget(refresh)

            cards_widget = QWidget()
            cards_layout = QVBoxLayout(cards_widget)
            self.cards = [LampCard(lamp, self) for lamp in lamps]
            for card in self.cards:
                cards_layout.addWidget(card)
            if not self.cards:
                empty = QLabel("No lamps configured.\nSee the README for the TinyTuya setup.")
                empty.setAlignment(Qt.AlignCenter)
                cards_layout.addWidget(empty)
            cards_layout.addStretch(1)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(cards_widget)

            central = QWidget()
            layout = QVBoxLayout(central)
            layout.addLayout(top)
            layout.addWidget(scroll, 1)
            self.setCentralWidget(central)

            count = len(self.cards)
            self.show_message(f"{count} lamp{'s' if count != 1 else ''} loaded, reading state…")
            self.refresh_all()

        def show_message(self, text: str):
            self.statusBar().showMessage(text, 10000)

        def set_all(self, on: bool):
            for card in self.cards:
                card.updating = True
                card.power.setChecked(on)
                card.updating = False
                card.worker.submit("power", on)

        def refresh_all(self):
            for card in self.cards:
                card.refresh()

        def closeEvent(self, event):
            for card in self.cards:
                card.worker.stop()
            super().closeEvent(event)

    # --- Theme -------------------------------------------------------------------

    def apply_theme(app: QApplication):
        """
        On KDE, keep the system Qt style (e.g. Oxygen) so the app matches
        Plasma. Elsewhere (e.g. Qtile) use Fusion with a dark palette.
        TUYA_LAMPS_THEME=dark|system overrides the automatic choice.
        """
        choice = os.environ.get("TUYA_LAMPS_THEME", "").lower()
        if choice not in ("dark", "system"):
            on_kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
            choice = "system" if on_kde else "dark"
        if choice == "system":
            return

        app.setStyle("Fusion")
        palette = QPalette()
        colours = {
            QPalette.Window: "#2b2b2b", QPalette.WindowText: "#e6e6e6",
            QPalette.Base: "#333333", QPalette.AlternateBase: "#3a3a3a",
            QPalette.ToolTipBase: "#404040", QPalette.ToolTipText: "#e6e6e6",
            QPalette.Text: "#e6e6e6", QPalette.Button: "#3c3c3c",
            QPalette.ButtonText: "#e6e6e6", QPalette.BrightText: "#ff5555",
            QPalette.Highlight: "#3d8ee0", QPalette.HighlightedText: "#ffffff",
            QPalette.Link: "#6aa9ff", QPalette.Mid: "#555555",
            QPalette.PlaceholderText: "#9a9a9a",
        }
        for role, colour in colours.items():
            palette.setColor(role, QColor(colour))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor("#7a7a7a"))
        app.setPalette(palette)

    # --- Start -------------------------------------------------------------------

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("tuya-lamps")  # lets Wayland match our .desktop file
    apply_theme(app)

    try:
        lamps = load_lamp_configs()
    except ConfigError as error:
        lamps = []
        folder = config_dir()
        QMessageBox.information(
            None, APP_NAME,
            f"<p>No lamp configuration found:<br><tt>{error}</tt></p>"
            "<p>Run the TinyTuya wizard once to create <tt>devices.json</tt>:</p>"
            f"<pre>cd {folder}\n"
            "~/.local/share/tuya-lamps/venv/bin/python -m tinytuya wizard</pre>"
            "<p>You need a Tuya IoT account linked to your Smart Life app. "
            "See the README for the step-by-step guide.</p>")

    window = MainWindow(lamps)
    window.show()
    return app.exec()


# ===========================================================================
# Entry point
# ===========================================================================

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        return cmd_list()
    if args.command == "set":
        validate_set_args(parser, args)
        return cmd_set(args)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
