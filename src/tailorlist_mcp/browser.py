"""The overview dashboard as a local web page, opened in the user's browser.

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

from tailorlist_mcp import settings

_TEMPLATE = Path(__file__).parent / "ui" / "overview.html"
_PLACEHOLDER = '<script id="tailorlist-data" type="application/json">null</script>'


def page_path() -> Path:
    return settings.data_dir() / "overview.html"


def write_page(data: dict, path: Path | None = None) -> Path:
    """Write the dashboard with `data` embedded. Returns the file path."""
    path = path or page_path()
    html = _TEMPLATE.read_text(encoding="utf-8")
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


def _open_command(path: Path) -> list[str] | None:
    if sys.platform == "darwin":
        return ["open", str(path)]
    if sys.platform.startswith("linux") and _is_wsl():
        win = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True).stdout.strip()
        if win and shutil.which("cmd.exe"):
            return ["cmd.exe", "/c", "start", '""', win]
        if shutil.which("wslview"):
            return ["wslview", str(path)]
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        return ["xdg-open", str(path)]
    return None


def open_in_browser(path: Path) -> bool:
    """Open the page in the default browser. Returns False when it couldn't (or is disabled)."""
    if os.environ.get("TAILORLIST_OPEN_BROWSER", "1").strip().lower() in ("0", "false", "no"):
        return False
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        cmd = _open_command(path)
        if not cmd:
            return False
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(path.parent) if not _is_wsl() else "/mnt/c", start_new_session=True,
        )
        return True
    except OSError:
        return False
