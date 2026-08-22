#!/usr/bin/env python3
"""Record the active Actions run, then apply the FKMS patch payload."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess


def record_actions_run() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    branch = os.environ.get("GITHUB_REF_NAME")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not branch or not run_id:
        raise SystemExit("GitHub Actions did not provide branch/run metadata")

    marker = Path("VC4_LINUX_FKMS_RUN.json")
    marker.write_text(
        json.dumps(
            {
                "branch": branch,
                "run_id": run_id,
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "source_sha": os.environ.get("GITHUB_SHA"),
                "workflow": os.environ.get("GITHUB_WORKFLOW"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"], check=True
    )
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    subprocess.run(["git", "add", "--", str(marker)], check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], check=False
    ).returncode
    if staged != 0:
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "ci/vc4: record firmware-KMS run [skip ci]",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"], check=True
        )


record_actions_run()
runpy.run_path(
    str(Path(__file__).with_name("apply-fkms-emulation-body.py")),
    run_name="__main__",
)
