"""Spin up the FastAPI service in a subprocess on a configurable port,
wait for /health, and shut it down cleanly on teardown.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


class ManagedHttpService:
    def __init__(
        self,
        *,
        port: int,
        env: dict[str, str],
        log_path: Path,
        startup_timeout: float = 60.0,
    ) -> None:
        self._port = port
        self._env = env
        self._log_path = log_path
        self._startup_timeout = startup_timeout
        self._proc: subprocess.Popen[str] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        full_env = {**os.environ, **self._env, "MEMORY_API_PORT": str(self._port)}
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self._log_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "agent_memory_lite"],
            cwd=str(REPO_ROOT),
            env=full_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + self._startup_timeout
        last_err = ""
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"HTTP service exited early with code {self._proc.returncode}; "
                    f"see log at {self._log_path}"
                )
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    return
                last_err = f"status={response.status_code}"
            except httpx.HTTPError as exc:
                last_err = str(exc)
            time.sleep(0.5)
        self.stop()
        raise RuntimeError(
            f"HTTP service did not become ready within {self._startup_timeout}s; "
            f"last error: {last_err}; log at {self._log_path}"
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
