#!/usr/bin/env python3
"""Build the source-code hand-off package.

    python packaging/make_source_package.py [--ref HEAD] [--out dist] [--allow-dirty]

Produces, in ``dist/``:

* ``livestock-counter-src-<version>.zip`` - every tracked file at ``--ref``
  (``git archive``, so no build output, node_modules or local settings),
  plus ``MANIFEST.sha256`` inside the archive listing each file's SHA-256;
* ``livestock-counter-src-<version>.zip.sha256`` - checksum of the archive.

The archive is byte-for-byte reproducible for a given commit (git archive
writes fixed timestamps), so the client can verify it against the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "livestock-counter"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="HEAD", help="git commit, tag or branch to package (default: HEAD)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--allow-dirty", action="store_true", help="package even with uncommitted changes")
    args = parser.parse_args()

    if not args.allow_dirty and args.ref == "HEAD" and git("status", "--porcelain").strip():
        print("error: uncommitted changes are not included in the package; commit them or pass --allow-dirty",
              file=sys.stderr)
        return 2

    version = tomllib.loads(git("show", f"{args.ref}:pyproject.toml").decode())["project"]["version"]
    commit = git("rev-parse", args.ref).decode().strip()
    root_dir = f"{PREFIX}-src-{version}"
    raw = git("archive", "--format=zip", f"--prefix={root_dir}/", args.ref)

    # Re-pack with a manifest of per-file checksums, keeping git's fixed timestamps.
    src = zipfile.ZipFile(io.BytesIO(raw))
    manifest = [f"# {PREFIX} {version} @ {commit}"]
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for info in src.infolist():
            data = src.read(info)
            dst.writestr(info, data)
            if not info.is_dir():
                manifest.append(f"{hashlib.sha256(data).hexdigest()}  {info.filename[len(root_dir) + 1:]}")
        stamp = src.infolist()[0].date_time
        entry = zipfile.ZipInfo(f"{root_dir}/MANIFEST.sha256", date_time=stamp)
        entry.compress_type = zipfile.ZIP_DEFLATED
        dst.writestr(entry, "\n".join(manifest) + "\n")

    args.out.mkdir(parents=True, exist_ok=True)
    archive = args.out / f"{root_dir}.zip"
    archive.write_bytes(out_buf.getvalue())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.out / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"{archive.relative_to(ROOT) if archive.is_relative_to(ROOT) else archive}: "
          f"{len(manifest) - 1} files, {archive.stat().st_size / 1024:.0f} KB, commit {commit[:10]}, sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
