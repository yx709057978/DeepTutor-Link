#!/usr/bin/env python3
"""Build a reviewable derived DeepTutor image; never deploy or recreate a container."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

EXPECTED_VERSION = "1.7.1"
EXPECTED_COMMIT = "f7aed0744c36c5afef16a4261c31ebee1208cac2"
EXPECTED_BASE = (
    "ghcr.io/hkuds/deeptutor:1.7.1@"
    "sha256:4982ca6bf43b98431366b43d1fec674a63656db3fbe49ba18c674bb6f19196dd"
)


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=False, text=True, capture_output=capture)
    if result.returncode:
        message = result.stderr or result.stdout or f"Command failed: {' '.join(command)}"
        raise SystemExit(message)
    return result.stdout if capture else ""


def build_wheel(root: Path, staging: Path) -> Path:
    environment = staging / "build-python"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run([str(python), "-I", "-m", "pip", "wheel", "--no-deps",
         "--wheel-dir", str(staging), str(root)])
    wheels = sorted(staging.glob("deeptutor_link-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected one newly built extension wheel, found {len(wheels)}.")
    return wheels[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=EXPECTED_BASE, help="Exact base image with @sha256 digest")
    parser.add_argument("--tag", required=True, help="New derived image tag; latest is refused")
    args = parser.parse_args()

    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", args.base):
        raise SystemExit("Base image must include an immutable @sha256 digest.")
    if args.tag == "latest" or args.tag.casefold().endswith(":latest"):
        raise SystemExit("Refusing to tag the derived image as latest.")

    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "src/deeptutor_link/assets/host-contract.json").read_text())
    if contract["version"] != EXPECTED_VERSION or contract["commit"] != EXPECTED_COMMIT:
        raise SystemExit("The extension contract is not the reviewed DeepTutor 1.7.1 baseline.")

    inspection = run(
        ["docker", "image", "inspect", args.base, "--format", "{{json .Config.Labels}}"],
        capture=True,
    )
    labels = json.loads(inspection)
    if labels.get("org.opencontainers.image.revision") != EXPECTED_COMMIT:
        raise SystemExit("Local base image revision does not match the pinned DeepTutor commit.")
    if labels.get("org.opencontainers.image.version") != EXPECTED_VERSION:
        raise SystemExit("Local base image version does not match the pinned DeepTutor release.")

    staging = Path(tempfile.mkdtemp(prefix="deeptutor-link-wheel-"))
    wheel = None
    try:
        wheel = build_wheel(root, staging)
        dist = root / "dist"
        dist.mkdir(exist_ok=True)
        shutil.copyfile(wheel, dist / wheel.name)
        context = staging / "docker-context"
        (context / "docker").mkdir(parents=True)
        (context / "dist").mkdir()
        shutil.copyfile(root / "docker/Dockerfile.link", context / "docker/Dockerfile.link")
        shutil.copyfile(root / ".dockerignore", context / ".dockerignore")
        shutil.copyfile(wheel, context / "dist" / wheel.name)
        run(
            [
                "docker",
                "build",
                "--file",
                str(context / "docker/Dockerfile.link"),
                "--build-arg",
                f"BASE_IMAGE={args.base}",
                "--build-arg",
                f"WHEEL_NAME={wheel.name}",
                "--tag",
                args.tag,
                str(context),
            ]
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if wheel is None:
        raise AssertionError("wheel must be set after a successful build")
    print(
        json.dumps(
            {
                "base_image": args.base,
                "derived_image": args.tag,
                "wheel": str(root / "dist" / wheel.name),
                "deployment": "Deploy with the canonical original compose plus override.",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
