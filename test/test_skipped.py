import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path

from awghr import Workflow
from awghr.cli import cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

# -- Test that independent jobs can be skipped

w = Workflow(name="skipped1")

# Add jobs
for exit_code in (0, 77):
    w.add_job(
        args=dict(exit_code=exit_code),
        cmds=[
            "bash -c "
            + shlex.quote(
                "echo DONE > file{exit_code}.txt"
                if exit_code == 0
                else "exit {exit_code}"
            ),
        ],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["file{exit_code}.txt"],
    )

w.write()

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check workflow state is SUCCESS")
statefile = w.workdir / "batch" / w.ID / "workflow_state"
assert statefile.is_file()
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check job states are as expected")
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    jobargs = json.load((jobdir / "job_args.json").open())
    jobstate = (jobdir / "job_state").read_text().strip()
    if jobargs["exit_code"] == 77:
        assert jobstate == "SUCCESS/SKIPPED"
    else:
        assert jobstate == "SUCCESS"

logger.info("check that output file were (not) created as expected")
outfiledir = w.workdir / "outputs"
assert (outfiledir / "file0.txt").is_file()
assert not (outfiledir / "file77.txt").is_file()

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-A")
print("-----", flush=True)

# -- Test that dependent jobs cannot be skipped

w = Workflow(name="skipped2")

# Add jobs
j1 = w.add_job(
    args=dict(exit_code=77),
    cmds=[
        "bash -c 'echo DONE > file{exit_code}.txt; exit {exit_code}'",
    ],
    time=1 * w.MIN,
    mem=10 * w.MB,
    disk=10 * w.MB,
    outputs=["file{exit_code}.txt"],
)
w.add_job(
    args=dict(exit_code=0),
    cmds=[
        "bash -c 'echo DONE > file{exit_code}.txt; exit {exit_code}'",
    ],
    time=1 * w.MIN,
    mem=10 * w.MB,
    disk=10 * w.MB,
    needs=[j1],
    outputs=["file{exit_code}.txt"],
)

w.write()

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check workflow state is SUCCESS")
statefile = w.workdir / "batch" / w.ID / "workflow_state"
assert statefile.is_file()
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check job states are as expected")
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    jobstate = (jobdir / "job_state").read_text().strip()
    assert jobstate == "SUCCESS/SKIPPED"

logger.info("check that output file were not created as expected")
outfiledir = w.workdir / "outputs"
assert not (outfiledir / "file0.txt").is_file()
assert not (outfiledir / "file77.txt").is_file()

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-A")
print("-----", flush=True)
