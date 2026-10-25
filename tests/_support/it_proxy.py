"""Fault-injecting HTTP proxy between the `omac` process and the Multica backend (PLAN 6.3.2).

Every request is recorded. A rule matches ``method`` + a path regex + the Nth matching request and then:

  fail_before   answer 502 without forwarding           -> the write never happened
  drop_after    forward, then cut the connection        -> the write happened, the caller never learns it
  kill_after    forward, then SIGKILL ``kill_pid``      -> the write happened and the process died
  delay         sleep ``seconds`` before forwarding
"""
from __future__ import annotations

import http.client
import os
import re
import signal
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@dataclass
class Rule:
    method: str                # "GET", "POST", ... or "WRITE" for any non-GET request
    path: str                  # regex searched in the request path
    action: str
    nth: int = 1               # 1 = first matching request
    seconds: float = 0.0
    seen: int = 0
    fired: bool = False


@dataclass
class Proxy:
    target: str
    rules: list = field(default_factory=list)
    log: list = field(default_factory=list)   # (method, path, status or None)
    kill_pid: int | None = None
    _server: ThreadingHTTPServer | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "Proxy":
        proxy, upstream = self, urlsplit(self.target)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _handle(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else None
                rule = proxy._match(self.command, self.path)
                if rule and rule.action == "fail_before":
                    proxy.log.append((self.command, self.path, 502))
                    self.send_response(502)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if rule and rule.action == "delay":
                    time.sleep(rule.seconds)
                conn = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=120)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                proxy.log.append((self.command, self.path, response.status))
                if rule and rule.action == "kill_after" and proxy.kill_pid:
                    os.kill(proxy.kill_pid, signal.SIGKILL)
                if rule and rule.action in ("drop_after", "kill_after"):
                    self.close_connection = True
                    self.connection.close()
                    return
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(key, value)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def _match(self, method: str, path: str) -> Rule | None:
        with self._lock:
            for rule in self.rules:
                if (rule.method == method or (rule.method == "WRITE" and method != "GET")) and re.search(rule.path, path):
                    rule.seen += 1
                    if rule.seen == rule.nth and not rule.fired:
                        rule.fired = True
                        return rule
        return None

    def writes(self) -> list[tuple[str, str, int | None]]:
        return [row for row in self.log if row[0] != "GET"]
