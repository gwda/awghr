# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test that a timed out job fails gracefully."""

import logging
import os
import stat
import subprocess
import sys
from pathlib import Path

from awghr import Workflow
from awghr._manage import cli as manage_cli

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
    args=dict(sleep=90),
    cmds=["sleep {sleep}"],
    time=(1, "min"),
    mem=(10, "MB"),
    disk=(10, "MB"),
    outputs=[],
)

w.write()

submit_script_file = w.workdir / "submit"
submit_script_mode = stat.S_IMODE(os.stat(submit_script_file).st_mode)
os.chmod(submit_script_file, submit_script_mode | stat.S_IWUSR)
with submit_script_file.open("r") as f:
    submit_script = f.read()
submit_script = submit_script.replace(sys.executable, "coverage run -a")
with submit_script_file.open("w") as f:
    f.write(submit_script)
os.chmod(submit_script_file, submit_script_mode)

logger.info("submit workflow and wait for it to time out")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "await", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
statefile = w.workdir / "workflows" / w.ID / "state"
assert statefile.is_file()
submittedfile = w.workdir / "workflows" / w.ID / "submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is FAILED")
state = statefile.read_text().strip()
assert state == "FAILED"

logger.info("check job state is FAILED")
joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    jobstate = (jobdir / "job_state").read_text().strip()
    assert jobstate == "FAILED"

logger.info("show job resources before any changes")
manage_cli("-F", "-r", workflow_dir=w.workdir)

logger.info("increase job time limit")
print("-----", flush=True)
try:
    manage_cli("-F", "-r", "+T", "9", workflow_dir=w.workdir)
    msg = "should have exited"
    raise AssertionError(msg)
except SystemExit:
    pass
print("-----", flush=True)
try:
    manage_cli("-S", "-r", "+T", "9min", workflow_dir=w.workdir)
    msg = "should have exited"
    raise AssertionError(msg)
except SystemExit:
    pass
print("-----", flush=True)
manage_cli("-F", "-r", "+T", "9min", "-M", "15MB", workflow_dir=w.workdir)

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "await", w.workdir / "queue"], check=True)

logger.info("check workflow state is SUCCESS")
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check job state is SUCCESS")
joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    jobstate = (jobdir / "job_state").read_text().strip()
    assert jobstate == "SUCCESS"
