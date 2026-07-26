"""Minimal WebSocket transport for signal-cli-rest-api's receive stream."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import select
import socket
import struct
import urllib.parse
from typing import Any


LOG = logging.getLogger(os.getenv("BOT_DISPLAY_NAME", "snorse-bot"))


class SignalWebSocket:
    """Small RFC 6455 client for the local, unencrypted Signal REST endpoint."""

    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise ValueError("Signal WebSocket URL must use ws://")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
        self.socket: socket.socket | None = None
        self.buffer = bytearray()
        self.fragments: bytearray | None = None

    def __enter__(self) -> SignalWebSocket:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        sock = socket.create_connection((self.host, self.port), timeout=10)
        sock.settimeout(1)
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Signal WebSocket closed during handshake")
            response.extend(chunk)
            if len(response) > 65536:
                raise ConnectionError("Signal WebSocket handshake was too large")
        headers, remainder = bytes(response).split(b"\r\n\r\n", 1)
        status = headers.split(b"\r\n", 1)[0]
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        )
        header_map = {
            name.strip().lower(): value.strip()
            for name, value in (
                line.split(b":", 1)
                for line in headers.split(b"\r\n")[1:]
                if b":" in line
            )
        }
        if status != b"HTTP/1.1 101 Switching Protocols":
            raise ConnectionError(
                f"Signal WebSocket handshake failed: {status.decode()}"
            )
        if header_map.get(b"sec-websocket-accept") != expected:
            raise ConnectionError("Signal WebSocket returned an invalid accept key")
        self.socket = sock
        self.buffer.extend(remainder)
        return self

    def __exit__(self, *_: object) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def _read_exactly(self, length: int) -> bytes:
        assert self.socket is not None
        while len(self.buffer) < length:
            chunk = self.socket.recv(max(4096, length - len(self.buffer)))
            if not chunk:
                raise ConnectionError("Signal WebSocket connection closed")
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        assert self.socket is not None
        mask = secrets.token_bytes(4)
        header = bytearray([0x80 | opcode, 0x80 | len(payload)])
        header.extend(mask)
        header.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.socket.sendall(header)

    def receive_text(self) -> str | None:
        """Return one text message, or None on a socket timeout/control frame."""
        try:
            assert self.socket is not None
            if not self.buffer and not select.select([self.socket], [], [], 1)[0]:
                return None
            self.socket.settimeout(10)
            first, second = self._read_exactly(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exactly(8))[0]
            mask = self._read_exactly(4) if second & 0x80 else None
            payload = self._read_exactly(length)
            if mask:
                payload = bytes(
                    byte ^ mask[index % 4] for index, byte in enumerate(payload)
                )
            if opcode == 0x8:
                raise ConnectionError("Signal WebSocket closed")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                return None
            if opcode == 0xA:
                return None
            if opcode == 0x1:
                if final:
                    return payload.decode("utf-8")
                self.fragments = bytearray(payload)
                return None
            if opcode == 0x0 and self.fragments is not None:
                self.fragments.extend(payload)
                if final:
                    completed = bytes(self.fragments)
                    self.fragments = None
                    return completed.decode("utf-8")
                return None
            if opcode != 0x1:
                LOG.warning(
                    "Ignoring unsupported Signal WebSocket frame opcode=%d", opcode
                )
                return None
        except socket.timeout:
            raise ConnectionError("Signal WebSocket frame timed out")
        finally:
            if self.socket is not None:
                self.socket.settimeout(1)


def websocket_messages(payload: str) -> list[dict[str, Any]]:
    """Normalize one WebSocket event to the native receive response shape."""
    decoded = json.loads(payload)
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    if not isinstance(decoded, dict):
        return []
    params = decoded.get("params")
    if decoded.get("method") == "receive" and isinstance(params, dict):
        result = params.get("result")
        if isinstance(result, dict):
            return [result]
        return [params]
    return [decoded]
