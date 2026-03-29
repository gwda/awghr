# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test that a workflow which exceeds queue limits fails gracefully."""

import logging
import subprocess
import sys
import time
from pathlib import Path

from awghr import Workflow

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

w = Workflow("qlimit", account="default")

if w.batch_system not in ("pbs", "slurm"):
    logger.warning(
        "skip test on batch systems that do not enforce queue limits (%s)",
        w.batch_system,
    )
    sys.exit(0)

prev_id = None
for i in range(1500):
    prev_id = w.add_job(
        args=dict(index=i),
        cmds=["echo DONE > {_out}"],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        needs=([prev_id] if prev_id is not None else []),
        outputs=["out_{index}.txt"],
    )

w.write()

logger.info("submit workflow and wait for its submission to fail")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "5"], check=True)

queue_no_output_when_no_jobs = {"slurm": ["squeue", "--noheader"], "pbs": ["qstat"]}
while True:
    logger.info("waiting until no jobs are running ...")
    queue_output = subprocess.check_output(
        queue_no_output_when_no_jobs[w.batch_system], text=True
    ).strip()
    if len(queue_output) == 0:
        break
    time.sleep(2)

subprocess.run([w.workdir / "queue"], check=True)

logger.info("check for workflow files")
statefile = w.workdir / "workflows" / w.ID / "state"
assert statefile.is_file()
submittedfile = w.workdir / "workflows" / w.ID / "submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is FAILED")
state = statefile.read_text().strip()
assert state == "FAILED"

logger.info("check job state files were not created")
joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert not (jobdir / "job_state").is_file()

logger.info("check output files were not created")
outputlistfile = w.workdir / "workflows" / w.ID / "outputs"
assert joblistfile.is_file()
for outname in [Path(line) for line in outputlistfile.read_text().split()]:
    assert not (w.workdir / "outputs" / outname).is_file()
