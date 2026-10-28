"""How the bridge reaches the board : Bluetooth LE, or the USB cable (#82).

Both give the same thing to :class:`~palamede_chessnut.bridge.Bridge` : board
frames in the Bluetooth format, handed to ``on_frame``, and ``write`` for a
command (LED frame, :data:`~palamede_chessnut.protocol.ENABLE_REPORTING`).
Only one at a time : while a Bluetooth central is subscribed, the board sends
its placement over Bluetooth only, and the cable just charges (#82, 27/10).

Over USB the board is a HID device (``2d80:8202``) with a vendor collection
(usage page ``0xff00``). Each report starts with its id, which is the first
byte of the Bluetooth frame : ``01`` placement, ``2a`` battery ; ``21`` and
``0a`` are written as is. Only the placement frame differs : ``01 3d``
(length 61) instead of ``01 24``, with the same 32 placement bytes.

* Linux : the ``/dev/hidrawN`` node, read and written as a file ; no extra
  package, but the session user needs access to it (udev rule, README).
* macOS, Windows : ``hidapi`` (extra ``usb``). On macOS the board is also
  a keyboard, held by the system : it is opened non-exclusively.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import select
import sys
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from palamede_chessnut.protocol import (
    BOARD_HEADER,
    BOARD_UUID,
    COMMAND_UUID,
    ENABLE_REPORTING,
)

log = logging.getLogger(__name__)

DEVICE_PREFIX = "Chessnut"
USB_VENDOR, USB_PRODUCT = 0x2D80, 0x8202
#: ``HID_ID`` of the board in ``/sys/class/hidraw/*/device/uevent``.
HIDRAW_ID = f"0003:{USB_VENDOR:08X}:{USB_PRODUCT:08X}"
VENDOR_USAGE_PAGE = 0xFF00
REPORT_BYTES = 64
#: How long a USB read waits, so that closing the link is noticed.
READ_SECONDS = 0.5
MISSING_HIDAPI = "USB on this system needs hidapi : install palamede-chessnut[usb]"

OnFrame = Callable[[Any, bytearray], None]


class BoardNotFound(Exception):
    """No board on this transport right now : look again later."""


class Session(Protocol):
    name: str

    def connected(self) -> bool: ...

    async def write(self, command: bytes) -> None: ...


def from_usb(report: bytes) -> bytes:
    """A USB report as the Bluetooth frame it carries : ``01 3d`` + placement → ``01 24`` + placement."""
    if len(report) >= 34 and report[0] == BOARD_HEADER[0]:
        return BOARD_HEADER + report[2:]
    return report


# ---- Bluetooth LE ------------------------------------------------------------


class _BleSession:
    def __init__(self, client: Any, name: str) -> None:
        self._client = client
        self.name = name

    def connected(self) -> bool:
        return bool(self._client.is_connected)

    async def write(self, command: bytes) -> None:
        await self._client.write_gatt_char(COMMAND_UUID, command)


@contextlib.asynccontextmanager
async def ble(on_frame: OnFrame) -> AsyncIterator[Session]:
    from bleak import BleakClient, BleakScanner

    device = await BleakScanner.find_device_by_filter(
        lambda d, _ad: (d.name or "").startswith(DEVICE_PREFIX), timeout=20
    )
    if device is None:
        raise BoardNotFound(f"no {DEVICE_PREFIX} board in range")
    async with BleakClient(device, timeout=20) as client:
        await client.start_notify(BOARD_UUID, on_frame)
        await client.write_gatt_char(COMMAND_UUID, ENABLE_REPORTING)
        yield _BleSession(client, str(device.name))


def ble_errors() -> tuple[type[BaseException], ...]:
    from bleak.exc import BleakError

    return (BleakError, TimeoutError, OSError)


# ---- USB ---------------------------------------------------------------------


class UsbDevice(Protocol):
    name: str

    def read(self) -> bytes:
        """One report, or ``b""`` after :data:`READ_SECONDS` without one ; ``OSError`` once gone."""
        ...

    def write(self, command: bytes) -> None: ...

    def close(self) -> None: ...


class HidrawDevice:
    """Linux : the board's ``/dev/hidrawN`` node, no extra package."""

    def __init__(self, path: str) -> None:
        self.name = f"{DEVICE_PREFIX} (USB, {path})"
        try:
            self._fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise PermissionError(
                f"no access to {path} : add the udev rule of the README, then replug the board"
            ) from None

    def read(self) -> bytes:
        ready, _, _ = select.select([self._fd], [], [], READ_SECONDS)
        return os.read(self._fd, REPORT_BYTES) if ready else b""

    def write(self, command: bytes) -> None:
        os.write(self._fd, command)

    def close(self) -> None:
        os.close(self._fd)


def find_hidraw(sysfs: Path = Path("/sys/class/hidraw")) -> str:
    """``/dev/hidrawN`` of the board, checked by its USB ids before anything is written."""
    for node in sorted(sysfs.glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text()
        except OSError:
            continue
        if f"HID_ID={HIDRAW_ID}" in uevent.split():
            return f"/dev/{node.name}"
    raise BoardNotFound("no Chessnut board on USB")


class HidapiDevice:
    """macOS, Windows : the vendor collection, through hidapi."""

    def __init__(self, hid: Any, path: bytes) -> None:
        self.name = f"{DEVICE_PREFIX} (USB)"
        self._device = hid.device()
        try:
            self._device.open_path(path)
        except OSError:
            hint = " : allow Input Monitoring for this terminal" if sys.platform == "darwin" else ""
            raise OSError(f"the board's USB interface cannot be opened{hint}") from None

    def read(self) -> bytes:
        return bytes(self._device.read(REPORT_BYTES, int(READ_SECONDS * 1000)))

    def write(self, command: bytes) -> None:
        if self._device.write(list(command)) < 0:
            raise OSError("USB write refused")

    def close(self) -> None:
        self._device.close()


def _non_exclusive(hid: Any) -> None:
    """macOS : the system holds the board as a keyboard ; an exclusive open fails (#82, 26/10)."""
    import ctypes

    try:
        ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)
    except (OSError, AttributeError) as exc:
        log.warning("hidapi : non-exclusive open not available (%s)", exc)


def open_hidapi() -> HidapiDevice:
    try:
        import hid
    except ImportError:
        raise BoardNotFound(MISSING_HIDAPI) from None
    if sys.platform == "darwin":
        _non_exclusive(hid)
    vendor = [
        d
        for d in hid.enumerate(USB_VENDOR, USB_PRODUCT)
        if d.get("usage_page") == VENDOR_USAGE_PAGE
    ]
    if not vendor:
        raise BoardNotFound("no Chessnut board on USB")
    return HidapiDevice(hid, vendor[0]["path"])


def open_usb() -> UsbDevice:
    if sys.platform.startswith("linux"):
        return HidrawDevice(find_hidraw())
    return open_hidapi()


class _UsbSession:
    def __init__(self, device: UsbDevice) -> None:
        self._device = device
        self.name = device.name
        self.lost: BaseException | None = None
        self.stop = threading.Event()

    def connected(self) -> bool:
        return self.lost is None and not self.stop.is_set()

    async def write(self, command: bytes) -> None:
        await asyncio.to_thread(self._device.write, command)

    def read_loop(self, loop: asyncio.AbstractEventLoop, on_frame: OnFrame) -> None:
        """Reader thread : each report to ``on_frame`` on the loop, until stopped or unplugged."""
        try:
            while not self.stop.is_set():
                report = self._device.read()
                if report:
                    loop.call_soon_threadsafe(on_frame, None, bytearray(from_usb(report)))
        except OSError as exc:
            self.lost = exc


@contextlib.asynccontextmanager
async def usb(
    on_frame: OnFrame, opener: Callable[[], UsbDevice] = open_usb
) -> AsyncIterator[Session]:
    device = await asyncio.to_thread(opener)
    session = _UsbSession(device)
    reader = threading.Thread(
        target=session.read_loop, args=(asyncio.get_running_loop(), on_frame), daemon=True
    )
    try:
        reader.start()
        await session.write(ENABLE_REPORTING)
        yield session
    finally:
        session.stop.set()
        await asyncio.to_thread(reader.join, 2 * READ_SECONDS)
        device.close()
    if session.lost is not None:
        raise OSError(f"USB link lost ({session.lost})")


def usb_errors() -> tuple[type[BaseException], ...]:
    return (OSError,)


#: ``--transport`` → (connector, errors that mean « link lost, try again »).
TRANSPORTS: dict[
    str, tuple[Callable[[OnFrame], Any], Callable[[], tuple[type[BaseException], ...]]]
] = {
    "ble": (ble, ble_errors),
    "usb": (usb, usb_errors),
}
