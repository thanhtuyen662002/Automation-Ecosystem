from __future__ import annotations

import argparse
from pathlib import Path


SECRET_MARKERS = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "LICENSE_KEY_PEPPER",
    "MACHINE_HASH_PEPPER",
    "APP_MACHINE_SALT",
    "SUPABASE_DB_URL",
)


def scan_file(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    hits: list[str] = []
    for marker in SECRET_MARKERS:
        if marker.encode("utf-8") in data:
            hits.append(marker)
    return hits


def iter_files(root: Path):
    if not root.exists():
        return
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan release artifacts for forbidden secret marker names.")
    parser.add_argument("paths", nargs="*", default=["ui/dist", "backend_dist", "release"])
    args = parser.parse_args()

    findings: list[tuple[Path, list[str]]] = []
    for raw_path in args.paths:
        for path in iter_files(Path(raw_path)):
            hits = scan_file(path)
            if hits:
                findings.append((path, hits))

    if findings:
        print("Forbidden secret marker names found in release artifacts:")
        for path, hits in findings:
            print(f"- {path}: {', '.join(hits)}")
        raise SystemExit(1)
    print("Release secret scan passed.")


if __name__ == "__main__":
    main()
