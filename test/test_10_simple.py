# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running a simple workflow."""

import logging
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from awghr import Workflow, WorkflowException
from awghr import _cli as workflow_cli
from awghr._manage import cli as manage_cli


@contextmanager
def in_directory(wd):
    owd = os.getcwd()
    try:
        os.chdir(wd)
        yield
    finally:
        os.chdir(owd)


# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

w = Workflow("sub", "simple", account="default", dated=True)

infile = Path.cwd() / "prefix.txt"
infile.write_text("XYZ\n")

# Add 10 independent jobs
for i in range(10):
    w.add_job(
        args=dict(index=i, const=10, sec=(0.1 if w.batch_system == "local" else 5)),
        cmds=["""
            if [ $MYCPUS -ne 1 ]; then
                echo 'MYCPUS!=1' >&2
                exit 1
            fi
            echo $HOME/sub/simple
            echo ${{TESTVAR}}1 >&2
            echo ${{TESTVAR}}2 >&2
            echo ${{TESTVAR}}3 >&2
            echo xyz >&2
            echo `cat {_in[prefix: ]}`{index} > out_{index}.txt
            echo 'DO NOT STAGE OUT' > temp_{index}.dat
            sleep {sec}
            """],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        inputs=[infile],
        outputs=["out_{index}.txt"],
    )

w.write()

logger.info("check generated files")
for f in (w.workdir / "workflows" / w.ID).iterdir():
    assert f.name in ("all_jobs", "batch_scripts", "outputs", "source")
for f in (w.workdir / "workflows" / w.ID / "source").iterdir():
    assert f.name == "workflow.json"

logger.info("check for job files that should (not) exist before submission")
joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert not (jobdir / "job_batchID").is_file()
    assert not (jobdir / "job_state").is_file()
    assert (jobdir / "job_args.json").is_file()
    assert (jobdir / "job_script.sh").is_file()

logger.info("test command line interface")
print("-----", flush=True)
assert manage_cli("-P", workdir=w.workdir) == 0
print("-----", flush=True)

logger.info("check that workflow is not archived")
assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
assert not archive_tarfile.is_file()

logger.info("submit workflow and wait for it to finish")
with in_directory(w.workdir):
    subprocess.run(["./submit"], check=True)
    subprocess.run(
        [
            "./wait",
            "-n",
            "1",
            "coverage",
            "run",
            "-a",
            "-m",
            "awghr._manage",
            "-A",
            "-@",
            "10",
        ],
        check=True,
    )

logger.info("check for workflow files")
statefile = w.workdir / "workflows" / w.ID / "state"
assert statefile.is_file()
submittedfile = w.workdir / "workflows" / w.ID / "submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is SUCCESS")
state = statefile.read_text().strip()
assert state == "SUCCESS"

# remove statefile to get full output from successful workflow
statefile.unlink()

logger.info("test command line interface")
print("-----", flush=True)
assert manage_cli("--clear", "-Y", workdir=w.workdir) == 0
print("-----", flush=True)
manage_cli(
    "-Ae",
    "-n",
    "1",
    "-u",
    "wall clock",
    "maximum",
    workdir=w.workdir,
)
print("-----", flush=True)
assert (
    manage_cli("-f", "index < 5 and nonexistent is None", "-Awjloz", workdir=w.workdir)
    == 0
)
print("-----", flush=True)
assert (
    manage_cli("-g", "this string was not printed", "-Awjloz", workdir=w.workdir) == 0
)
print("-----", flush=True)
assert manage_cli("-Ai", "dummy_batch_ID_for_testing", workdir=w.workdir) == 0
print("-----", flush=True)
try:
    manage_cli("-n", "0", workdir=w.workdir)
    msg = "should have exited"
    raise AssertionError(msg)
except SystemExit:
    pass
print("-----", flush=True)
assert manage_cli(workdir=Path("/tmp")) == 1
print("-----", flush=True)

logger.info("check that state is re-created")
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
    assert (jobdir / "job_args.json").is_file()
    assert (jobdir / "job_batchID").is_file()
    assert (jobdir / "job_script.sh").is_file()
    assert (jobdir / "job_state").is_file()
    assert (jobdir / "job_usage").is_file()

logger.info("check that job standard output/error/usage printed what was expected")
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    stdout = (jobdir / "job_stdout").read_text().strip()
    stderr = (jobdir / "job_stderr").read_text().strip()
    usage = (jobdir / "job_usage").read_text().strip()
    assert stdout == str(w.workdir.parent)
    assert stderr == "\n".join(["abc1", "abc2", "abc3", "xyz"])
    assert "User time" in usage

logger.info("check that no extra files were created in job directory")
jobfile_regex = "|".join(
    [
        r"job_args\.json",
        r"job_batchID",
        r"job_script\.sh",
        r"job_state",
        r"job_std(out|err)(_(pre|post))?",
        r"job_usage",
        r"out_[0-9]\.txt",
    ]
)
archive_files = set()
archive_files.add(joblistfile)
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    archive_files.add(jobdir)
    for jobfile in jobdir.iterdir():
        archive_files.add(jobfile)
        assert jobfile.is_file()
        assert re.fullmatch(jobfile_regex, jobfile.name) is not None

logger.info("check that successful workflow cannot be rewritten")
try:
    w.write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

for j in range(2):
    logger.info("test archiving workflow (#%i)", j + 1)
    assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
    archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
    assert archive_tarfile.is_file()
    tar_output = subprocess.check_output(["tar", "-tf", archive_tarfile], text=True)
    archive_files_from_tar = set(
        [w.workdir / Path(line) for line in tar_output.split()]
    )
    assert archive_files == archive_files_from_tar
    assert not (w.workdir / "jobs").is_dir()
