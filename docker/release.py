#!/usr/bin/env python3
"""Package the current local viewer + Docker deployment, never data or credentials."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def sources():
    # Repository-level instructions/configuration travel with future source releases.
    metadata = [ROOT/name for name in ("README.md", ".gitignore", ".gitattributes", "THIRD_PARTY_NOTICES.md") if (ROOT/name).is_file()]
    viewer = ROOT / "offline_anatomy_viewer"
    top = {".py", ".js", ".html", ".css", ".md", ".bat", ".command"}
    files = [p for p in viewer.iterdir() if p.is_file() and p.suffix in top]
    for folder in (viewer/"assets/module-icons", viewer/"translations"):
        files += [p for p in folder.rglob("*") if p.is_file() and p.suffix in {".png", ".json", ".md"}]
    files += [ROOT/n for n in ("anatomy_identity.py", "overlay_capture.py", "overlay_capture.js", "overlay_runtime.js")]
    for path in (ROOT/"docker").rglob("*"):
        rel = path.relative_to(ROOT/"docker")
        if not path.is_file() or any(p in {"__pycache__", "state", "backups", "releases"} for p in rel.parts):
            continue
        if path.name == ".env" or path.suffix in {".pyc", ".sqlite3", ".zip", ".key"}:
            continue
        if path.suffix in {".py", ".html", ".css", ".js", ".cjs", ".sh", ".md", ".svg", ".toml", ".yaml", ".service", ".timer"} or path.name in {"Dockerfile", "Dockerfile.dockerignore", "Caddyfile", ".env.example", "requirements.txt"}:
            files.append(path)
    return sorted(set(files + metadata))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT/"releases"/f"atlas-source-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sources():
            name = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix in {".sh", ".command"} else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
            records.append(hashlib.sha256(content).hexdigest() + "  " + name)
        archive.writestr("SOURCE_SHA256.txt", "\n".join(records) + "\n")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
    print(f"RELEASE=PASS; files={len(records)}; data=excluded; credentials=excluded; path={output}")


if __name__ == "__main__":
    main()
