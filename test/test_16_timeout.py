# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test that a timed out job fails gracefully."""

import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

w = Workflow(name="timeout")

if w.batch_system not in ("pbs", "slurm"):
    logger.warning(
        "skip test on batch systems that do not enforce time limits (%s)",
        w.batch_system,
    )
    sys.exit(0)

w.add_job(
    args=dict(sleep=1000),
    cmds=["sleep {sleep}"],
    time=(1, "min"),
    mem=(10, "MB"),
    disk=(10, "MB"),
    outputs=[],
)

w.write()

logger.info("submit workflow and wait for it to time out")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
statefile = w.workdir / "batch" / w.ID / "workflow_state"
assert statefile.is_file()
submittedfile = w.workdir / "batch" / w.ID / "workflow_submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is FAILED")
state = statefile.read_text().strip()
assert state == "FAILED"

logger.info("check job state is FAILED")
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    jobstate = (jobdir / "job_state").read_text().strip()
    assert jobstate == "FAILED"
