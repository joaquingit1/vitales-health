"""`camera-twin`: a simulated network camera. It speaks what an IP camera speaks: an MJPEG stream over HTTP
(`multipart/x-mixed-replace`, one JPEG per part), so the worker's real network camera source reads it unchanged.

Routes, one virtual camera per scenario (scenes.SCENARIOS):
- `GET /cameras/<scenario>/stream.mjpg`: the stream, paced in real time from the moment the client connects.
- `GET /cameras/<scenario>/identity` and `GET /identity`: the device identity document.

The device declares itself simulated in its identity: the identity document says `"origin_mode": "SIM"` and every
stream response carries the header `X-Device-Origin: SIM`, which is where the worker's source reads a network camera's
origin (a real camera sends no such header and is LIVE). Nothing else in the pipeline knows a twin exists.

The process prints one line `camera twin listening on http://HOST:PORT` once it accepts connections (port 0 picks a
free port). Each connection streams until the client leaves, except the stream_drop scenario, which hangs up.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from crew_health_camera_twin.sample import SAMPLE, cache_dir, cached
from crew_health_camera_twin.scenes import (
    SCENARIOS,
    STREAM_DROP_AFTER_S,
    Frame,
    Scenario,
    Synthetic,
    base_frame,
    encode,
    sample_frames,
)

ORIGIN_HEADER = "X-Device-Origin"
BOUNDARY = "frame"
MAX_CLIENTS = 16


def identity(scenario: Scenario | None) -> dict[str, object]:
    document: dict[str, object] = {
        "manufacturer": "crew-health",
        "model": "camera twin",
        "origin_mode": "SIM",
        "simulated": True,
        "scenarios": sorted(SCENARIOS),
        "sample": {"url": SAMPLE.url, "license": SAMPLE.license, "attribution": SAMPLE.attribution},
    }
    if scenario is not None:
        document |= {
            "device_id": "twin_" + scenario.name,
            "scenario": scenario.name,
            "injected_pulse_per_min": scenario.pulse_per_min,
            "injected_breaths_per_min": scenario.breaths_per_min,
            "fault": scenario.fault,
        }
    return document


class Twin:
    def __init__(self, video: Path, base: Frame, *, fps: float, seed: int) -> None:
        self.video, self.base, self.fps, self.seed = video, base, fps, seed

    def frames(self, scenario: Scenario) -> Iterator[tuple[float, Frame]]:
        if scenario.kind == "sample":
            yield from sample_frames(self.video, self.fps)
            return
        scene = Synthetic(self.base, scenario, seed=self.seed)
        index = 0
        while True:
            t = index / self.fps
            if scenario.fault == "stream_drop" and t >= STREAM_DROP_AFTER_S:
                return
            yield t, scene.frame(t)
            index += 1


def handler(twin: Twin) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if parts == ["identity"]:
                return self._json(identity(None))
            if len(parts) == 3 and parts[0] == "cameras" and parts[1] in SCENARIOS:
                scenario = SCENARIOS[parts[1]]
                if parts[2] == "identity":
                    return self._json(identity(scenario))
                if parts[2] == "stream.mjpg":
                    return self._stream(scenario)
            self.send_error(404)

        def _json(self, document: dict[str, object]) -> None:
            body = json.dumps(document, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(ORIGIN_HEADER, "SIM")
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, scenario: Scenario) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header(ORIGIN_HEADER, "SIM")
            self.send_header("X-Device-Id", "twin_" + scenario.name)
            self.end_headers()
            started = time.monotonic()
            try:
                for t, image in twin.frames(scenario):
                    delay = started + t - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    data = encode(image)
                    head = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n"
                    self.wfile.write(head.encode("ascii") + data + b"\r\n")
                    self.wfile.flush()
            except BrokenPipeError, ConnectionResetError:
                return

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_CLIENTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated network camera (MJPEG over HTTP).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cache", type=Path, default=None, help="sample cache folder")
    args = parser.parse_args()
    video = cached(args.cache or cache_dir())
    twin = Twin(video, base_frame(video), fps=args.fps, seed=args.seed)
    server = Server((args.host, args.port), handler(twin))
    print(f"camera twin listening on http://{args.host}:{server.server_address[1]}", flush=True)
    sys.stdout.flush()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
