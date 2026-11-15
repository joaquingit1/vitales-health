# /// script
# requires-python = ">=3.14"
# ///
"""One command for the demo: `uv run scripts/demo.py`.

Resets a demo hub with simulated crew, habitat feeds and a camera twin (every reading labeled Sim or
Replay), serves it, and serves the crew app against it. Ctrl-C stops both. Needs uv and pnpm. Ports come
from HUB_PORT (8080), APP_PORT (5173), CAMERA_PORT (8853) and TWIN_PORT (8854).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "state" / "demo"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def port(name: str, default: int) -> str:
    return str(int(os.environ.get(name, default)))


def run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, timeout=1800)


def main() -> None:
    uv, pnpm = shutil.which("uv"), shutil.which("pnpm")
    if uv is None or pnpm is None:
        sys.exit("Needs uv and pnpm on PATH.")
    hub_port, app_port = port("HUB_PORT", 8080), port("APP_PORT", 5173)
    if not PYTHON.is_file():  # the potency engine's environment; the medicines view reads its results
        run(uv, "venv", "-q", str(ROOT / ".venv"))
        run(uv, "pip", "install", "-q", "--python", str(PYTHON), "-e", str(ROOT))
    run(pnpm, "install", "--silent")
    hub = ["--directory", str(ROOT / "hub"), "crew-health", "demo"]
    run(
        uv,
        "run",
        *hub,
        "reset",
        "--dir",
        str(DEMO),
        "--camera-port",
        port("CAMERA_PORT", 8853),
        "--twin-port",
        port("TWIN_PORT", 8854),
    )
    server = subprocess.Popen([uv, "run", *hub, "serve", "--dir", str(DEMO), "--port", hub_port])
    try:
        credentials = json.loads((DEMO / "credentials.json").read_text(encoding="utf-8"))
        print(f"\nOpen http://127.0.0.1:{app_port}/")
        for who, secret in credentials.items():
            print(f"  {who:<14}{secret}")
        print("Pair the screen with stn_galley, then sign in as anyone above.\n", flush=True)
        app_env = {**os.environ, "CREW_HUB_ORIGIN": f"http://127.0.0.1:{hub_port}"}
        subprocess.run(
            [pnpm, "-C", str(ROOT / "ui/crew"), "exec", "vite", "--port", app_port, "--strictPort"],
            env=app_env,
            check=False,
        )
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
