# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running a simple workflow."""

import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow
from awghr._cli import cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

w = Workflow(name="simple")

infile = Path.cwd() / "prefix.txt"
infile.write_text("XYZ")

# Add 10 independent jobs
for i in range(10):
    w.add_job(
        args=dict(index=i, const=10),
        cmds=[
            """
            !bash
            if [ $MYCPUS -ne 1 ]; then
                echo 'MYCPUS!=1' >&2
                exit 1
            fi
            echo $HOME
            echo ${{TESTVAR}}1 >&2
            echo ${{TESTVAR}}2 >&2
            echo ${{TESTVAR}}3 >&2
            echo xyz >&2
            echo `cat {_in[prefix]}`{index} > out_{index}.txt
            """
        ],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[infile],
        outputs=["out_{index}.txt"],
    )

w.write()

logger.info("check generated files")
for f in (w.workdir / "batch" / w.ID).iterdir():
    assert (
        f.name in ("workflow_all_jobs", "workflow_outputs")
        or f.name.startswith("batch_")
        or f.name.startswith("job_")
    )

logger.info("check for job files that should (not) exist before submission")
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert not (jobdir / "job_batchID").is_file()
    assert not (jobdir / "job_state").is_file()
    assert (jobdir / "job_args.json").is_file()
    assert (jobdir / "job_script.sh").is_file()

logger.info("test command line interface")
print("-----", flush=True)
cli("-d", str(w.workdir), "-P")
print("-----", flush=True)

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
statefile = w.workdir / "batch" / w.ID / "workflow_state"
assert statefile.is_file()
submittedfile = w.workdir / "batch" / w.ID / "workflow_submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is SUCCESS")
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check that state is re-created")
statefile.unlink()
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait"], check=True)
state = statefile.read_text().strip()
assert state == "SUCCESS"
assert not submittedfile.is_file()

for i in range(10):
    outfile = w.workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == f"XYZ{i}"

logger.info("check for job files that should exist after submission")
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert (jobdir / "job_batchID").is_file()
    assert (jobdir / "job_state").is_file()
    assert (jobdir / "job_args.json").is_file()
    assert (jobdir / "job_script.sh").is_file()

logger.info("check that job standard output/error printed what was expected")
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    stdout = (jobdir / "job_stdout").read_text().strip()
    stderr = (jobdir / "job_stderr").read_text().strip()
    assert stdout == str(w.workdir.parent)
    assert stderr == "\n".join(["abc1", "abc2", "abc3", "xyz"])

logger.info("check that re-writing workflow does not remove successful states")
w.write()
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert (jobdir / "job_state").is_file()

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-Ae", "-n", "1", "-g", "a")
print("-----", flush=True)
cli("-d", str(w.workdir), "-f", "index < 5", "-Abjloz")
print("-----", flush=True)
try:
    cli("jobs", "-d", str(w.workdir), "-n", "0")
    msg = "should have exited"
    raise AssertionError(msg)
except SystemExit:
    pass
try:
    cli("jobs")
    msg = "should have raised a FileNotFoundError"
    raise AssertionError(msg)
except FileNotFoundError:
    pass
