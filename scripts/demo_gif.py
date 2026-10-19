"""Record docs/img/demo.gif: handle the booking sample, show the trace, approve it in the queue, open the calendar.

Runs the app locally with the fake provider on a temporary copy of data/ (the repository's data never changes), drives
the review UI with Playwright and assembles the screenshots with Pillow.

Usage: python scripts/demo_gif.py [--out docs/img/demo.gif] [--width 1200] [--height 820]
Needs: pip install playwright pillow && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 3 * 1024 * 1024


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_app(tmp: Path) -> tuple[subprocess.Popen, str]:
    """The app on a copy of data/ with the fake provider; returns the process and the base URL."""
    data = tmp / "data"
    shutil.copytree(ROOT / "data" / "kb", data / "kb")
    shutil.copytree(ROOT / "data" / "samples", data / "samples")
    for name in ("calendar.json", "fixtures_today.txt"):
        shutil.copy(ROOT / "data" / name, data / name)  # crm.sqlite is rebuilt at start-up
    port = _free_port()
    env = {**os.environ, "INBOX_FAKE_PROVIDER": "1", "INBOX_DATA_DIR": str(data), "BUDGET_FILE": str(tmp / ".budget.json"), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(base + "/health", timeout=1.0).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("the app did not start")


def record(base: str, width: int, height: int) -> list[tuple[Image.Image, int]]:
    """Screenshots with hold times in ms: inbox → handled → trace → queue → approved → calendar."""
    from playwright.sync_api import sync_playwright

    frames: list[tuple[Image.Image, int]] = []

    def shot(hold_ms: int) -> None:
        png = page.screenshot(full_page=False)
        frames.append((Image.open(io.BytesIO(png)).convert("RGB"), hold_ms))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.goto(base + "/ui", wait_until="domcontentloaded")  # Gradio keeps a connection open: never "networkidle"
        page.get_by_role("button", name="Handle").wait_for(timeout=30_000)
        time.sleep(0.8)
        shot(2200)  # the inbox with the booking sample selected
        page.get_by_role("button", name="Handle").click()
        page.get_by_text("Intent:").wait_for(timeout=30_000)
        time.sleep(0.8)
        shot(3000)  # intent, summary, draft
        trace = page.get_by_text("Trace (tool calls only", exact=False).first
        trace.scroll_into_view_if_needed()
        time.sleep(0.6)
        shot(2800)  # the trace viewer
        approve = page.get_by_role("button", name="Approve").first
        approve.scroll_into_view_if_needed()
        time.sleep(0.5)
        shot(2200)  # the queue with the pending book_slot
        approve.click()
        page.get_by_text("executed").wait_for(timeout=15_000)
        time.sleep(0.8)
        shot(2800)  # approved: the queue is empty, the result line shows "executed"
        page.get_by_text("Show calendar").click()
        time.sleep(1.0)
        page.get_by_text("Show calendar").scroll_into_view_if_needed()
        shot(3200)  # the calendar expander with the new booking
        browser.close()
    return frames


def assemble(frames: list[tuple[Image.Image, int]], out: Path, width: int) -> int:
    """GIF under 3 MB: adaptive palette, downscaled if needed. Returns the size in bytes."""
    scale = 1.0
    while True:
        imgs = []
        for img, _ in frames:
            w = int(width * scale)
            h = int(img.height * w / img.width)
            imgs.append(img.resize((w, h), Image.LANCZOS).quantize(colors=128, method=Image.Quantize.MEDIANCUT))
        out.parent.mkdir(parents=True, exist_ok=True)
        imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=[hold for _, hold in frames], loop=0, optimize=True)
        size = out.stat().st_size
        if size <= MAX_BYTES or scale < 0.5:
            return size
        scale *= 0.85


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record the demo GIF against a local fake-provider run.")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "demo.gif"))
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=820)
    args = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="inbox-demo-"))
    proc, base = start_app(tmp)
    try:
        frames = record(base, args.width, args.height)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
    size = assemble(frames, Path(args.out), args.width)
    total_s = sum(hold for _, hold in frames) / 1000
    print(f"{args.out}: {len(frames)} frames, {total_s:.1f} s, {size / 1024:.0f} KB")
    return 0 if size <= MAX_BYTES else 1


if __name__ == "__main__":
    raise SystemExit(main())
