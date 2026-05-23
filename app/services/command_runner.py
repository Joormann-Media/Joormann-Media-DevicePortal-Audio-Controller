from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List


@dataclass(slots=True)
class CommandResult:
    command: str
    success: bool
    return_code: int
    output: str
    error: str


def _build_audio_env() -> Dict[str, str]:
    """Return an env dict that guarantees XDG_RUNTIME_DIR is set.

    wpctl / pactl / pw-cli need XDG_RUNTIME_DIR to reach the per-user
    PipeWire/PulseAudio socket.  When the app runs as a systemd service
    (system scope, not --user) that variable is absent from the process
    environment, so all audio commands fail silently.
    """
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        uid = os.getuid()
        candidate = f"/run/user/{uid}"
        if os.path.isdir(candidate):
            env["XDG_RUNTIME_DIR"] = candidate
    return env


class CommandRunner:
    def run(self, command: List[str], timeout: int = 4) -> CommandResult:
        cmd = " ".join(command)
        if shutil.which(command[0]) is None:
            return CommandResult(cmd, False, 127, "", f"{command[0]} not found")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_build_audio_env(),
            )
            return CommandResult(
                command=cmd,
                success=completed.returncode == 0,
                return_code=completed.returncode,
                output=(completed.stdout or "").strip(),
                error=(completed.stderr or "").strip(),
            )
        except Exception as exc:  # pragma: no cover
            return CommandResult(cmd, False, 1, "", str(exc))
