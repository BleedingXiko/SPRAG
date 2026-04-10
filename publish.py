#!/usr/bin/env python3
"""Local release helper for publishing spragkit to PyPI."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INIT_FILE = ROOT / "sprag" / "__init__.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
DEFAULT_REPOSITORY = "pypi"


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def read_version() -> str:
    content = INIT_FILE.read_text(encoding="utf-8")
    match = re.search(r"^__version__ = ['\"]([^'\"]+)['\"]$", content, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find __version__ in sprag/__init__.py")
    return match.group(1)


def write_version(version: str) -> None:
    content = INIT_FILE.read_text(encoding="utf-8")
    updated = re.sub(
        r"^__version__ = ['\"][^'\"]+['\"]$",
        f'__version__ = "{version}"',
        content,
        flags=re.MULTILINE,
    )
    INIT_FILE.write_text(updated, encoding="utf-8")


def bump_version(version: str, bump: str) -> str:
    major, minor, patch = map(int, version.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def clean() -> None:
    shutil.rmtree(DIST_DIR, ignore_errors=True)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    for egg_info in ROOT.glob("*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)


def dist_files() -> list[str]:
    files = sorted(str(path) for path in DIST_DIR.glob("*"))
    if not files:
        raise RuntimeError("No distribution files were built in dist/")
    return files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and publish spragkit to PyPI.")
    parser.add_argument(
        "release",
        nargs="?",
        default=None,
        help="Version bump (patch/minor/major) or an explicit version like 0.2.0",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Bump version and build, but do not upload.",
    )
    parser.add_argument(
        "--testpypi",
        action="store_true",
        help="Upload to TestPyPI instead of PyPI.",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help="Twine repository alias from ~/.pypirc.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Pass --skip-existing to twine upload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    current = read_version()

    if args.release is not None:
        # Explicit bump requested on the command line.
        if args.release in {"major", "minor", "patch"}:
            target = bump_version(current, args.release)
        else:
            if not re.fullmatch(r"\d+\.\d+\.\d+", args.release):
                raise SystemExit("Release must be patch/minor/major or an explicit x.y.z version.")
            target = args.release
    else:
        # No explicit bump. Ask interactively if the version looks stale.
        print(f"Current version: {current}")
        answer = input("Bump version? (patch/minor/major/n) [patch]: ").strip().lower()
        if answer in {"", "patch", "minor", "major"}:
            target = bump_version(current, answer or "patch")
        elif answer in {"n", "no"}:
            target = current
        else:
            if not re.fullmatch(r"\d+\.\d+\.\d+", answer):
                raise SystemExit("Expected patch/minor/major, an explicit x.y.z, or 'n'.")
            target = answer

    if target != current:
        print(f"Updating version: {current} -> {target}")
        write_version(target)
    else:
        print(f"Version unchanged: {current}")

    clean()

    run([sys.executable, "-m", "pip", "install", "--upgrade", "build", "twine", "setuptools", "wheel"])
    run([sys.executable, "-m", "build"])

    files = dist_files()
    run([sys.executable, "-m", "twine", "check", *files])

    if args.build_only:
        print("\nBuild complete. Nothing was uploaded.")
        return

    upload_cmd = [sys.executable, "-m", "twine", "upload"]
    if args.testpypi:
        upload_cmd.extend(["--repository", "testpypi"])
    elif args.repository:
        upload_cmd.extend(["--repository", args.repository])
    if args.skip_existing:
        upload_cmd.append("--skip-existing")
    upload_cmd.extend(files)
    run(upload_cmd)

    final_version = read_version()
    print(f"\nspragkit {final_version} published.")


if __name__ == "__main__":
    main()
