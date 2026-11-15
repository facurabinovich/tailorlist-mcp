"""The dashboards (overview, playlist) as local web pages, opened in the user's browser.

Fallback for hosts that don't render MCP Apps (Claude Code, and Claude Desktop 4.23118.0 with
local servers). Same HTML as the MCP App, with the data embedded.

Opening the browser must not touch this process's stdout (the MCP channel), so the opener
runs with stdin/stdout/stderr detached.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from tailorlist_mcp import settings, theme

_PLACEHOLDER = '<script id="tailorlist-data" type="application/json">null</script>'


def page_path(kind: str = "overview") -> Path:
    return settings.data_dir() / f"{kind}.html"


def write_page(data: dict, path: Path | None = None, kind: str = "overview") -> Path:
    """Write the dashboard (ui/<kind>.html, themed by theme.py) with `data` embedded. Returns the file path."""
    path = path or page_path(kind)
    html = theme.template(kind)
    # "</" inside JSON would end the script tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(_PLACEHOLDER, f'<script id="tailorlist-data" type="application/json">{payload}</script>')
    path.write_text(html, encoding="utf-8")
    return path


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _open_command(target: str, is_path: bool = True) -> list[str] | None:
    if sys.platform == "darwin":
        return ["open", target]
    if sys.platform.startswith("linux") and _is_wsl():
        if is_path:
            win = subprocess.run(["wslpath", "-w", target], capture_output=True, text=True).stdout.strip()
        else:
            win = target
        if win and shutil.which("cmd.exe"):
            return ["cmd.exe", "/c", "start", '""', win]
        if shutil.which("wslview"):
            return ["wslview", target]
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        return ["xdg-open", target]
    return None


def _open_disabled() -> bool:
    return os.environ.get("TAILORLIST_OPEN_BROWSER", "1").strip().lower() in ("0", "false", "no")


def _launch(target: str, is_path: bool, cwd: str) -> bool:
    try:
        if platform.system() == "Windows":
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
            return True
        cmd = _open_command(target, is_path)
        if not cmd:
            return False
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=cwd if not _is_wsl() else "/mnt/c", start_new_session=True,
        )
        return True
    except OSError:
        return False


def open_in_browser(path: Path) -> bool:
    """Open the page in the default browser. Returns False when it couldn't (or is disabled)."""
    if _open_disabled():
        return False
    return _launch(str(path), True, str(path.parent))


def open_url(url: str) -> bool:
    """Open an http(s) URL in the default browser (same rules as open_in_browser)."""
    if _open_disabled() or not url.startswith(("http://", "https://")):
        return False
    return _launch(url, False, str(settings.data_dir()))
