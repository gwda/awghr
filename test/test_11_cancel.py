# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running a workflow that is cancelled and them resumed."""

import logging
import subprocess
import sys
import time
from pathlib import Path

from awghr import Workflow
from awghr._cli import cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

w = Workflow(name="cancel")

w.add_job(
    args=dict(sleep=1000),
    cmds=["!bash sleep {sleep}; echo DONE > {_out}"],
    time=1 * w.HOUR,
    mem=10 * w.MB,
    disk=10 * w.MB,
    outputs=["DONE.txt"],
)

w.write()

logger.info("submit workflow, wait for it to start running, then cancel it")
subprocess.run([w.workdir / "submit"], check=True)
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
jobstatefile = Path(joblistfile.read_text().strip()) / "job_state"
n = 0
while not jobstatefile.is_file():
    logger.info("waiting for %s to exist ...", jobstatefile)
    time.sleep(2)
    n += 1
    assert n < 100
subprocess.run([w.workdir / "cancel"], check=True)
statefile = w.workdir / "batch" / w.ID / "workflow_state"
n = 0
while not statefile.is_file():
    logger.info("waiting for %s to exist ...", statefile)
    time.sleep(2)
    n += 1
    assert n < 100

logger.info("check for workflow files")
assert statefile.is_file()
submittedfile = w.workdir / "batch" / w.ID / "workflow_submitted"
assert not submittedfile.is_file()

logger.info("check job state is FAILED")
jobstate = jobstatefile.read_text().strip()
assert jobstate == "FAILED"

logger.info("check workflow state is FAILED")
state = statefile.read_text().strip()
assert state == "FAILED"

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir))
print("-----", flush=True)

logger.info("check that re-writing workflow removes unsuccessful states")
w.write()
assert not jobstatefile.is_file()
