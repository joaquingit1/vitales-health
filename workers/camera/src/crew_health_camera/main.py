"""`crew-health-camera`: the worker process E20 supervises (SYSTEM 3: 127.0.0.1:8091, own /health).

Deployment settings come from a JSON file named by CREW_HEALTH_CAMERA_DEPLOYMENT: listen host and port, hub URL,
adapter ID, the two token files (hub to worker, worker to hub adapter token), the state directory that keeps
undelivered attempts across restarts, and the cameras. Each camera names its device ID, display name, kind
(personal or station), the person it is paired with (`subject_id`; a camera paired with nobody is never used) and its
`stream`: a capture index for a local camera or an http(s) MJPEG URL for a network camera. Optional: policy, settings
and model folder paths. A missing or invalid value refuses to start; there is no sample fallback, and the origin of
the readings is whatever the cameras declare (video_source.declared_origin).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import httpx
import uvicorn

from crew_health_camera.adapters.evidence_store import FileEvidenceStore
from crew_health_camera.adapters.http_app import build_app
from crew_health_camera.adapters.hub_client import HttpHubClient
from crew_health_camera.adapters.models import verified
from crew_health_camera.adapters.settings import DEFAULT_POLICY, DEFAULT_SETTINGS, load_policy, load_settings
from crew_health_camera.adapters.system_clock import SystemClock
from crew_health_camera.adapters.video_source import Stream, VideoSource
from crew_health_camera.application.ports import Camera
from crew_health_camera.application.service import AcquisitionService


def _secret(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _stream(value: object) -> Stream:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("camera stream must be an index or a URL")
    if isinstance(value, str) and not value.startswith(("http://", "https://")):
        raise ValueError("camera stream URL must be http or https")
    return value


def main() -> None:
    deployment = json.loads(Path(os.environ["CREW_HEALTH_CAMERA_DEPLOYMENT"]).read_text(encoding="utf-8"))
    policy = load_policy(Path(deployment.get("policy", DEFAULT_POLICY)))
    settings = load_settings(Path(deployment.get("settings", DEFAULT_SETTINGS)))
    clock = SystemClock(segment="seg_" + uuid.uuid4().hex, poll_ms=settings.runner_idle_ms)
    cameras = tuple(
        Camera(c["device_id"], c["name"], c["kind"], c.get("subject_id")) for c in deployment["cameras"]
    )
    models_dir = deployment.get("models")
    source = VideoSource(
        clock=clock,
        cameras=cameras,
        streams={c["device_id"]: _stream(c["stream"]) for c in deployment["cameras"]},
        models=verified(Path(models_dir) if models_dir else None),
        rules=policy.capture,
        timeout_s=settings.camera_timeout_ms / 1000,
    )
    hub = HttpHubClient(
        httpx.Client(base_url=deployment["hub_url"]),
        token=_secret(deployment["adapter_token_file"]),
        timeout_s=settings.hub_timeout_ms / 1000,
    )
    service = AcquisitionService(
        policy=policy, source=source, clock=clock, hub=hub, bounds=settings.bounds,
        receipt_ids=lambda: "rcp_" + uuid.uuid4().hex,
        evidence=FileEvidenceStore(Path(deployment["state_dir"]), max_files=settings.bounds.max_acquisitions),
    )  # fmt: skip
    stop = threading.Event()

    def run() -> None:
        while not stop.is_set():
            worked = service.run_next()
            service.deliver_pending()
            service.expire()
            if not worked:
                stop.wait(settings.runner_idle_ms / 1000)

    threading.Thread(target=run, name="camera-runner", daemon=True).start()
    app = build_app(
        service,
        adapter_id=deployment["adapter_id"],
        token=_secret(deployment["worker_token_file"]),
        max_body_bytes=settings.max_body_bytes,
    )
    try:
        uvicorn.run(app, host=deployment["host"], port=int(deployment["port"]), log_level="warning")
    finally:
        stop.set()
