"""Require a report from the packaged native Qt event loop, not a live PID."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if sys.platform == "win32":
        executable = root / "dist/PhotoFileManager/PhotoFileManager.exe"
        expected_platform = "windows"
    elif sys.platform == "darwin":
        executable = root / "dist/PhotoFileManager.app/Contents/MacOS/PhotoFileManager"
        expected_platform = "cocoa"
    else:
        executable = root / "dist/PhotoFileManager/PhotoFileManager"
        expected_platform = None
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("QT_") and name.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    with tempfile.TemporaryDirectory(prefix="pfm-native-startup-") as directory:
        report = Path(directory) / "startup.json"
        subprocess.run(
            [str(executable), "--self-test-report", str(report)],
            cwd=directory,
            env=env,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        if not result.get("native_window_created"):
            raise RuntimeError(f"Native window was not initialized: {result}")
        if expected_platform and result["platform"] != expected_platform:
            raise RuntimeError(f"Wrong Qt platform: {result}")
        print("Packaged native startup passed:", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
