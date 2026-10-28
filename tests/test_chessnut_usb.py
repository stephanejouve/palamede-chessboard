"""The board over its USB cable (#82) : frames recorded by Admin on 27/10, Linux hidraw."""

from __future__ import annotations

import asyncio
import logging
import sys
import types

import chess
import pytest
from palamede_chessnut import transport
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.protocol import ENABLE_REPORTING, decode_board, led_frame
from palamede_chessnut.status import snapshot

#: ``01 3d`` + placement after 1.Nf3 d5 (test-ubuntu-bt-002), counter, zero padding.
AFTER_NF3_D5 = bytes.fromhex(
    "013d a6b99c60 77777777 0000a000 00000000 00400000 00000000 44044444 58133285 4d08"
).ljust(62, b"\0")
START = bytes.fromhex(
    "013d a6b99c6a 77777777 00000000 00000000 00000000 00000000 44444444 58133285 8b04"
).ljust(62, b"\0")
BATTERY = bytes.fromhex("2a026401")


def test_usb_placement_is_the_bluetooth_one() -> None:
    board = chess.Board()
    board.push_san("Nf3")
    board.push_san("d5")
    placement = decode_board(transport.from_usb(AFTER_NF3_D5))
    assert placement is not None and placement.board_fen() == board.board_fen()
    assert decode_board(transport.from_usb(START)) == chess.BaseBoard()


def test_raw_usb_frame_is_not_mistaken_for_a_board() -> None:
    """The ``01 3d`` header alone is refused : only the conversion makes it a board frame."""
    assert decode_board(START) is None


def test_battery_report_passes_through() -> None:
    assert transport.from_usb(BATTERY) == BATTERY
    assert decode_board(transport.from_usb(BATTERY)) is None


# ---- Linux : finding the hidraw node -----------------------------------------


def _node(sysfs, name: str, hid_id: str) -> None:
    device = sysfs / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME=x\n")


def test_hidraw_node_is_found_by_its_usb_ids(tmp_path) -> None:
    _node(tmp_path, "hidraw0", "0003:000005AC:00000262")  # the Mac's own keyboard
    _node(tmp_path, "hidraw2", "0003:00002D80:00008202")
    assert transport.find_hidraw(tmp_path) == "/dev/hidraw2"


def test_no_board_on_usb(tmp_path) -> None:
    _node(tmp_path, "hidraw0", "0003:000005AC:00000262")
    with pytest.raises(transport.BoardNotFound):
        transport.find_hidraw(tmp_path)


def test_hidraw_without_access_says_how_to_fix_it(monkeypatch) -> None:
    def refused(*_args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(transport.os, "open", refused)
    with pytest.raises(PermissionError, match="udev rule"):
        transport.HidrawDevice("/dev/hidraw2")


# ---- macOS, Windows : hidapi -------------------------------------------------


def fake_hid(interfaces: list[dict], opened: list) -> types.ModuleType:
    module = types.ModuleType("hid")
    module.__file__ = "/nonexistent/hid.so"

    class Device:
        def open_path(self, path) -> None:
            opened.append(path)

        def close(self) -> None:
            pass

        def write(self, data) -> int:
            return len(data)

    module.enumerate = lambda vid, pid: interfaces  # type: ignore[attr-defined]
    module.device = Device  # type: ignore[attr-defined]
    return module


def test_hidapi_opens_the_vendor_collection_not_the_keyboard(monkeypatch) -> None:
    opened: list = []
    interfaces = [
        {"usage_page": 0x0001, "usage": 0x0006, "path": b"keyboard"},
        {"usage_page": 0xFF00, "usage": 0xFF00, "path": b"vendor"},
    ]
    monkeypatch.setitem(sys.modules, "hid", fake_hid(interfaces, opened))
    monkeypatch.setattr(transport.sys, "platform", "win32")
    transport.open_hidapi()
    assert opened == [b"vendor"]


def test_hidapi_without_vendor_collection_writes_nothing(monkeypatch) -> None:
    opened: list = []
    keyboard = [{"usage_page": 0x0001, "usage": 0x0006, "path": b"keyboard"}]
    monkeypatch.setitem(sys.modules, "hid", fake_hid(keyboard, opened))
    with pytest.raises(transport.BoardNotFound):
        transport.open_hidapi()
    assert opened == []


def test_missing_hidapi_says_which_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "hid", None)
    with pytest.raises(transport.BoardNotFound, match=r"palamede-chessnut\[usb\]"):
        transport.open_hidapi()


def test_macos_asks_for_a_non_exclusive_open(monkeypatch, caplog) -> None:
    """Exclusive opens fail on macOS (#82, 26/10) ; an old hidapi only warns."""
    opened: list = []
    vendor = [{"usage_page": 0xFF00, "usage": 0xFF00, "path": b"vendor"}]
    monkeypatch.setitem(sys.modules, "hid", fake_hid(vendor, opened))
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    with caplog.at_level(logging.WARNING):
        transport.open_hidapi()
    assert "non-exclusive" in caplog.text
    assert opened == [b"vendor"]


def test_refused_hidapi_write_is_a_lost_link() -> None:
    device = transport.HidapiDevice.__new__(transport.HidapiDevice)
    device._device = types.SimpleNamespace(write=lambda data: -1)
    with pytest.raises(OSError):
        device.write(led_frame(()))


# ---- the session, through the bridge -----------------------------------------


class FakeUsb:
    """Replays reports, then « is unplugged » (OSError) or idles."""

    name = "Chessnut (USB, fake)"

    def __init__(self, reports: list[bytes], unplug: bool = False) -> None:
        self.reports = list(reports)
        self.unplug = unplug
        self.written: list[bytes] = []
        self.closed = False

    def read(self) -> bytes:
        if self.reports:
            return self.reports.pop(0)
        if self.unplug:
            raise OSError("device disconnected")
        import time

        time.sleep(0.01)
        return b""

    def write(self, command: bytes) -> None:
        self.written.append(bytes(command))

    def close(self) -> None:
        self.closed = True


def usb_bridge(monkeypatch, device: FakeUsb) -> Bridge:
    def connect(on_frame):
        return transport.usb(on_frame, opener=lambda: device)

    monkeypatch.setitem(transport.TRANSPORTS, "usb", (connect, transport.usb_errors))
    return Bridge("https://viewer.test", "g1", chess.WHITE, "x", board_transport="usb")


def test_usb_frames_reach_the_bridge(monkeypatch) -> None:
    device = FakeUsb([BATTERY, START])
    bridge = usb_bridge(monkeypatch, device)
    seen: list = []

    async def tick(self, write) -> None:
        if self.observed is not None:
            seen.append((self.board_link, self.observed.board_fen()))
            self.stopped.set()

    monkeypatch.setattr(Bridge, "tick", tick)
    asyncio.run(asyncio.wait_for(bridge.run_board(), 5))
    assert seen == [("connected", chess.STARTING_BOARD_FEN)]
    assert device.written[0] == ENABLE_REPORTING
    assert device.written[-1] == led_frame(())  # LEDs off on the way out
    assert device.closed and bridge.board_link == "searching"


def test_unplugged_board_is_looked_for_again(monkeypatch, caplog) -> None:
    """Each loss after a connection is logged ; the board is opened again."""
    opened: list = []

    def connect(on_frame):
        device = FakeUsb([START], unplug=True)
        opened.append(device)
        if len(opened) == 3:
            bridge.stopped.set()
        return transport.usb(on_frame, opener=lambda: device)

    monkeypatch.setitem(transport.TRANSPORTS, "usb", (connect, transport.usb_errors))
    monkeypatch.setattr("palamede_chessnut.bridge.RETRY_SECONDS", 0)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "x", board_transport="usb")
    with caplog.at_level(logging.INFO):
        asyncio.run(asyncio.wait_for(bridge.run_board(), 5))
    assert len(opened) == 3 and all(d.closed for d in opened)
    lost = [r for r in caplog.records if "board link lost" in r.getMessage()]
    assert len(lost) == 3 and "device disconnected" in lost[0].getMessage()


def test_same_bluetooth_error_is_logged_once(monkeypatch, caplog) -> None:
    """Bluetooth off on the Mac : one line, not one every 3 s (smoke of 28/10)."""
    attempts: list = []

    class Off(Exception):
        pass

    def connect(on_frame):
        attempts.append(1)
        if len(attempts) == 4:
            bridge.stopped.set()
        raise Off("Bluetooth device is turned off")

    monkeypatch.setitem(transport.TRANSPORTS, "ble", (connect, lambda: (Off,)))
    monkeypatch.setattr("palamede_chessnut.bridge.RETRY_SECONDS", 0)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "x")
    with caplog.at_level(logging.INFO):
        asyncio.run(asyncio.wait_for(bridge.run_board(), 5))
    lost = [r for r in caplog.records if "turned off" in r.getMessage()]
    assert len(attempts) == 4 and len(lost) == 1


# ---- command line, page ------------------------------------------------------


@pytest.mark.parametrize(("args", "expected"), [([], "ble"), (["--transport", "usb"], "usb")])
def test_transport_option(monkeypatch, args, expected) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", "x")
    argv = ["--viewer", "https://viewer.test", "--game", "g1", "--color", "white", *args]
    bridge = bridge_from_args(argv, "test")
    assert bridge.board_transport == expected
    assert snapshot(bridge)["transport"] == expected
