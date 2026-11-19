"""Bounded socket reception and file writing outside the GUI thread."""
from __future__ import annotations
__VERSION__ = "0.1.0.2"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import copy
import logging
import os
import queue
import selectors
import socket
import threading
import time
from dataclasses import dataclass, field

from common.logger import MessageFileWriter
from core.config import ApplicationConfig, validate
from core.messages import parseMessage


@dataclass
class Client:
    peer: tuple
    buffer: bytearray = field(default_factory=bytearray)
    last_seen: float = field(default_factory=time.monotonic)


class LogServer:
    def __init__(self, config: ApplicationConfig):
        validate(config)
        self.config = copy.deepcopy(config)
        self.events = queue.Queue(maxsize=2000)
        self._stop = threading.Event()
        self.thread = None
        self.address = None
        self.failure = ""

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError("Server is already running.")
        self._stop.clear()
        self.failure = ""
        self.thread = threading.Thread(target=self._run, name="LogServer receiver", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def _emit(self, kind, value):
        if self._stop.is_set():
            try:
                self.events.put_nowait((kind, value))
            except queue.Full:
                pass
            return
        while not self._stop.is_set():
            try:
                self.events.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue

    def _deliver(self, payload, peer, transport, writer):
        if not payload:
            return
        message = parseMessage(payload, peer, transport, self.config.socket.encoding)
        writer.write(message)
        self._emit("message", message)

    def _run(self):
        settings = self.config.socket
        clients = {}
        writer = None
        selector = None
        try:
            selector = selectors.DefaultSelector()
            writer = MessageFileWriter(self.config.logging)
            family = socket.AF_INET6 if ":" in settings.host else socket.AF_INET
            transports = ("tcp", "udp") if settings.protocol == "both" else (settings.protocol,)
            for transport in transports:
                sock = socket.socket(family, socket.SOCK_STREAM if transport == "tcp" else socket.SOCK_DGRAM)
                try:
                    if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    elif transport == "tcp":
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if family == socket.AF_INET6:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, settings.receive_buffer)
                    sock.bind((settings.host, settings.port))
                    sock.setblocking(False)
                    if transport == "tcp":
                        sock.listen(settings.backlog)
                    selector.register(sock, selectors.EVENT_READ, transport)
                    self.address = sock.getsockname()
                except BaseException:
                    sock.close()
                    raise
            self._emit("started", self.address)
            while not self._stop.is_set():
                for key, _ in selector.select(0.1):
                    if self._stop.is_set():
                        break
                    sock, kind = key.fileobj, key.data
                    if kind == "tcp":
                        try:
                            conn, peer = sock.accept()
                        except BlockingIOError:
                            continue
                        except ConnectionError as exc:
                            self._emit("warning", f"Incoming connection failed: {exc}")
                            continue
                        if len(clients) >= settings.max_clients:
                            conn.close()
                            self._emit("warning", "Client limit reached; connection refused.")
                            continue
                        try:
                            conn.setblocking(False)
                            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, int(settings.keepalive))
                            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, settings.receive_buffer)
                            clients[conn] = Client(peer)
                            selector.register(conn, selectors.EVENT_READ, "client")
                        except OSError as exc:
                            clients.pop(conn, None)
                            conn.close()
                            self._emit("warning", f"Cannot configure client {peer[0]}: {exc}")
                            continue
                        self._emit("connected", peer)
                        self._emit("clients", len(clients))
                    elif kind == "udp":
                        try:
                            payload, peer = sock.recvfrom(min(65535, settings.max_message_bytes + 1))
                        except BlockingIOError:
                            continue
                        except OSError as exc:
                            if getattr(exc, "winerror", None) == 10040:
                                self._emit("warning", "Oversized UDP datagram rejected.")
                                continue
                            raise
                        if len(payload) > settings.max_message_bytes:
                            self._emit("warning", f"Oversized UDP datagram from {peer[0]} rejected.")
                        else:
                            self._deliver(payload, peer, "udp", writer)
                    else:
                        client = clients[sock]
                        try:
                            payload = sock.recv(min(settings.receive_buffer, 65536))
                        except BlockingIOError:
                            continue
                        except (ConnectionError, OSError) as exc:
                            self._emit("warning", f"Client {client.peer[0]} disconnected: {exc}")
                            self._disconnect(sock, clients, selector, writer)
                            continue
                        if not payload:
                            self._disconnect(sock, clients, selector, writer)
                            continue
                        client.last_seen = time.monotonic()
                        client.buffer.extend(payload)
                        while b"\n" in client.buffer:
                            line, _, tail = client.buffer.partition(b"\n")
                            client.buffer = bytearray(tail)
                            if len(line) > settings.max_message_bytes:
                                self._emit("warning", f"Oversized TCP message from {client.peer[0]}; connection closed.")
                                self._disconnect(sock, clients, selector, writer, flush=False)
                                break
                            self._deliver(line, client.peer, "tcp", writer)
                        if sock in clients and len(client.buffer) > settings.max_message_bytes:
                            self._emit("warning", f"TCP message limit exceeded by {client.peer[0]}; connection closed.")
                            self._disconnect(sock, clients, selector, writer, flush=False)
                if settings.idle_timeout:
                    now = time.monotonic()
                    for conn, client in list(clients.items()):
                        if now - client.last_seen >= settings.idle_timeout:
                            self._emit("warning", f"Idle client {client.peer[0]} disconnected.")
                            self._disconnect(conn, clients, selector, writer)
            for conn in list(clients):
                self._disconnect(conn, clients, selector, writer)
        except (OSError, ValueError) as exc:
            self.failure = str(exc)
            self._emit("error", self.failure)
        except Exception:
            logging.getLogger("LogServer").exception("Unexpected receiver failure")
            self.failure = "Unexpected receiver failure; see application.log for traceback."
            self._emit("error", self.failure)
        finally:
            if selector is not None:
                for key in list(selector.get_map().values()):
                    key.fileobj.close()
                selector.close()
            if writer is not None:
                try:
                    writer.close()
                except OSError as exc:
                    self.failure = f"Cannot close log files: {exc}"
                    self._emit("error", self.failure)
            self._emit("stopped", None)

    def _disconnect(self, conn, clients, selector, writer, *, flush=True):
        client = clients.pop(conn)
        try:
            if flush and client.buffer:
                self._deliver(bytes(client.buffer), client.peer, "tcp", writer)
        finally:
            selector.unregister(conn)
            conn.close()
        self._emit("disconnected", client.peer)
        self._emit("clients", len(clients))
