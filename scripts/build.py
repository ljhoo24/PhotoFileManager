"""Build in an isolated Windows DLL search environment, then verify startup."""

import os
import subprocess
import sys
from pathlib import Path


def build_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in tuple(env):
        if name.upper() in {"PYTHONPATH", "PYTHONHOME"} or name.upper().startswith("QT_"):
            env.pop(name)
    if sys.platform == "win32":
        windows = Path(env.get("SystemRoot", r"C:\Windows"))
        # PyInstaller searches PATH when resolving DLLs. Do not bundle DLLs from
        # unrelated image tools, SDKs, Conda, or the calling agent's runtime.
        paths = [
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        ]
        env["PATH"] = os.pathsep.join(str(path) for path in paths)
    return env


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dist = root / "dist"
    work = root / "build" / "pyinstaller"
    # PyInstaller --noconfirm may replace its own previous output directory.
    for target in (dist, dist / "PhotoFileManager", work):
        if not target.resolve().is_relative_to(root):
            raise RuntimeError(f"Build output must stay inside the project: {target}")
    env = build_environment()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--noupx",
            "--windowed",
            "--name",
            "PhotoFileManager",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            "--specpath",
            str(root / "build"),
            str(root / "src/photo_file_manager/__main__.py"),
        ],
        cwd=root,
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(root / "scripts/smoke_test.py")],
        cwd=root,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
