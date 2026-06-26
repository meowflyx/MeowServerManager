"""Server management — start, stop, restart, status of the Minecraft server."""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _detect_run_script(server_dir: Path) -> str:
    bat = server_dir / "run.bat"
    sh = server_dir / "run.sh"
    if platform.system() == "Windows" and bat.is_file():
        return "run.bat"
    if sh.is_file():
        return "run.sh"
    if bat.is_file():
        return "run.bat"
    return "run.sh"


def _build_command(run_script: Path) -> list[str]:
    if run_script.suffix == ".bat":
        return ["cmd", "/c", str(run_script)]
    return ["bash", str(run_script)]


class ServerManager:
    def __init__(self, server_dir: str, run_script: str = "run.sh") -> None:
        self.server_dir = Path(server_dir).resolve()
        if not self.server_dir.is_dir():
            actual = Path(server_dir).resolve()
            if actual.is_dir():
                self.server_dir = actual
            else:
                self.server_dir = Path(server_dir).resolve()

        self.run_script_name = run_script
        if not (self.server_dir / run_script).is_file():
            detected = _detect_run_script(self.server_dir)
            logger.info("Script '%s' not found, using '%s'", run_script, detected)
            self.run_script_name = detected

        self.run_script = self.server_dir / self.run_script_name
        self._process: subprocess.Popen | None = None

    def _validate(self) -> None:
        if not self.server_dir.is_dir():
            raise FileNotFoundError(f"Server directory not found: {self.server_dir}")
        if not self.run_script.is_file():
            raise FileNotFoundError(f"Run script not found: {self.run_script}")
        if platform.system() != "Windows":
            if not os.access(self.run_script, os.X_OK):
                raise PermissionError(f"Run script is not executable: {self.run_script}")

    def start(self) -> int:
        self._validate()

        if self._process is not None and self._process.poll() is None:
            logger.warning("Server is already running (pid=%d)", self._process.pid)
            return self._process.pid

        cmd = _build_command(self.run_script)
        logger.info("Starting server: %s (cwd=%s)", " ".join(cmd), self.server_dir)

        if platform.system() == "Windows":
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.server_dir),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.server_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        logger.info("Server started with pid=%d", self._process.pid)
        return self._process.pid

    def stop(self, timeout: int = 60) -> bool:
        if self._process is None or self._process.poll() is not None:
            logger.warning("Server is not running")
            return True

        logger.info("Stopping server (pid=%d)", self._process.pid)

        if platform.system() == "Windows":
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self._process.send_signal(signal.SIGINT)

        try:
            self._process.wait(timeout=timeout)
            logger.info("Server stopped gracefully")
            return True
        except subprocess.TimeoutExpired:
            logger.warning("Server did not stop gracefully, sending SIGKILL")
            self._process.kill()
            self._process.wait()
            return True

    def restart(self) -> int:
        self.stop()
        time.sleep(2)
        return self.start()

    def status(self) -> dict:
        running = self._process is not None and self._process.poll() is None
        return {
            "running": running,
            "pid": self._process.pid if self._process else None,
            "server_dir": str(self.server_dir),
            "run_script": str(self.run_script),
        }

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
