# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test command-line interface help."""

import os
import subprocess

from awghr import Workflow

# Write a dummy workflow
w = Workflow(name="cli_help")
w.add_job(
    args=dict(),
    cmds=[":"],
    time=(10, "min"),
    mem=(10, "MB"),
    disk=(10, "MB"),
    outputs=[],
)
w.write()

# Find commands
cmds = ["awghr-config"]
for f in sorted(w.workdir.iterdir()):
    if f.is_file() and os.access(f, os.X_OK):
        cmds.append(str(f))

# Show command help
for cmd in cmds:
    print("-----", cmd, flush=True)
    help_str = subprocess.check_output([cmd, "-h"], text=True)
    assert help_str.startswith("usage: ")
    print(help_str.strip(), flush=True)
