from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from taxedo.security import check_upload, check_text, CONVERSION_TIMEOUT, UserInputError


def _worker_error(output: bytes) -> str | None:
    try:
        error = json.loads(output).get("error")
    except (ValueError, AttributeError):
        return None
    return error if isinstance(error, str) and error else None


async def extract_attachment(
    data: bytes, filename: str, *, timeout: float = CONVERSION_TIMEOUT
) -> str:
    check_upload(len(data))
    extension = Path(filename).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="taxedo-convert-") as directory:
        source = Path(directory) / "input"
        source.write_bytes(data)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": directory,
            "TMPDIR": directory,
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-E",
            "-s",
            str(Path(__file__).with_name("worker.py")),
            str(source),
            extension,
            cwd=directory,
            env=environment,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        if process.returncode:
            raise UserInputError(
                _worker_error(output)
                or "Document conversion failed. Try a clearer image or another supported format."
            )
        text = json.loads(output)["text"]
        check_text(text)
        return text
