"""Backward-compatible launcher for the Radiology Atlas offline viewer."""

from server import main


if __name__ == "__main__":
    raise SystemExit(main())
