import sys

# Suppress native loader error dialogs during automated startup verification;
# the process must fail rather than appear alive behind an error dialog.
if sys.platform == "win32" and "--self-test-report" in sys.argv:
    import ctypes

    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)

from photo_file_manager.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
