from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class CommandResult:
    command: str
    success: bool
    return_code: int
    output: str
    error: str


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
