# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running a DAG workflow that fails and then recovers."""

import logging
import re
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

# These files are created to cause jobs to fail
fail_files = []
for i in range(3):
    fail_file = Path.cwd() / f"FAIL{i}"
    fail_file = fail_file.absolute()
    fail_file.touch()
    fail_files.append(fail_file)
fail_file_index = 0


# Return a command that will fail if a given file exists
def get_fail_cmd(cmd):
    global fail_files
    global fail_file_index
    fail_file = fail_files[fail_file_index]
    cmd = (
        f"if test -f '{fail_file}'; then echo '{fail_file} exists!' >&2; exit 1; fi; "
        + cmd
    )
    fail_file_index = (fail_file_index + 1) % len(fail_files)
    return cmd


w = Workflow(name="dag_fail", tags=["maxarray"])

# Build up files containing up to 3 digits
for level in range(3):
    if level == 0:
        jobs = []
        for digit in range(3):
            job_ID = w.add_job(
                args=dict(stage="build", digit=digit),
                cmds=["echo {digit} > {_out}"],
                time=(10, "min"),
                mem=(10, "MB"),
                disk=(10, "MB"),
                outputs=["f-{_id}.txt"],
            )
            jobs.append(job_ID)
    else:
        prev_jobs = jobs
        jobs = []
        for prev_job_ID in prev_jobs:
            for digit in range(3):
                job_ID = w.add_job(
                    args=dict(
                        stage="build",
                        prev_job_ID=prev_job_ID,
                        prev_digit=w[prev_job_ID, "digit"],
                        digit=digit,
                    ),
                    cmds=[
                        get_fail_cmd("echo `cat {_in}`{digit} > {_out}"),
                    ],
                    time=(10, "min"),
                    mem=(10, "MB"),
                    disk=(10, "MB"),
                    inputs=[(prev_job_ID, "f-{_id}.txt")],
                    outputs=["f-{_id}.txt"],
                )
                jobs.append(job_ID)

# Sort files containing digits pairwise
jobs = sorted(jobs)
while len(jobs) > 1:
    prev_jobs = jobs
    jobs = []
    while len(prev_jobs) > 0:
        if len(prev_jobs) > 1:
            prev_job_ID_1 = prev_jobs.pop()
            prev_job_ID_2 = prev_jobs.pop()
            job_ID = w.add_job(
                args=dict(
                    stage="sort",
                    prev_job_ID_1=prev_job_ID_1,
                    prev_job_ID_2=prev_job_ID_2,
                ),
                cmds=[
                    get_fail_cmd("cat {_in} | sort > {_out}"),
                ],
                time=(10, "min"),
                mem=(10, "MB"),
                disk=(10, "MB"),
                inputs=[(prev_job_ID_1, "f-{_id}.txt"), (prev_job_ID_2, "f-{_id}.txt")],
                outputs=["f-{_id}.txt"],
            )
            jobs.append(job_ID)
        else:
            prev_job_ID = prev_jobs.pop()
            job_ID = w.add_job(
                args=dict(
                    stage="sort",
                    prev_job_ID=prev_job_ID,
                ),
                cmds=[
                    "cat {_in} > {_out}",
                ],
                time=(10, "min"),
                mem=(10, "MB"),
                disk=(10, "MB"),
                inputs=[(prev_job_ID, "f-{_id}.txt")],
                outputs=["f-{_id}.txt"],
            )
            jobs.append(job_ID)


w.write()

while True:
    logger.info(
        "submit workflow and wait for it to finish (%i fail files exist)",
        len(fail_files),
    )
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait", w.workdir / "queue"], check=True)

    logger.info("check for workflow files (%i fail files exist)", len(fail_files))
    joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
    assert joblistfile.is_file()
    statefile = w.workdir / "batch" / w.ID / "workflow_state"
    assert statefile.is_file()

    failedjoblistfile = w.workdir / "batch" / w.ID / "workflow_failed_jobs"
    if len(fail_files) == 0:
        assert not failedjoblistfile.is_file()
    else:
        assert failedjoblistfile.is_file()
        logger.info("check that failed files are correctly listed")
        for jobdir in [
            Path(line) for line in failedjoblistfile.read_text().splitlines()
        ]:
            stdout_file = jobdir / "job_stdout"
            stderr_file = jobdir / "job_stderr"
            if stdout_file.is_file() and stderr_file.is_file():
                stdout = stdout_file.read_text().strip()
                stderr = stderr_file.read_text().strip()
                assert stdout == ""
                assert re.search(r"FAIL[012] exists!$", stderr) is not None
            else:
                assert (not stdout_file.is_file()) and (not stderr_file.is_file())

    expected_state = "FAILED" if len(fail_files) > 0 else "SUCCESS"
    logger.info(
        "check state is %s (%i fail files exist)", expected_state, len(fail_files)
    )
    state = statefile.read_text().strip()
    assert state == expected_state

    logger.info("test command line interface")
    print("-----", flush=True)
    cli("jobs", "-d", str(w.workdir), "-e")
    print("-----", flush=True)
    tmpstatefile = statefile.replace(statefile.with_suffix(".tmp"))
    cli("jobs", "-d", str(w.workdir), "-e")
    tmpstatefile.replace(statefile)
    print("-----", flush=True)

    # Successively remove fail files so that jobs can pass
    if len(fail_files) == 0:
        break
    fail_file = fail_files.pop(0)
    fail_file.unlink()

outfile = w.workdir / "outputs" / f"f-{jobs[0]}.txt"
logger.info("check that output file '%s' is present and correct", outfile)
assert outfile.is_file()
digits = outfile.read_text().splitlines()
line = 0
for i in range(3):
    for j in range(3):
        for k in range(3):
            assert digits[line] == f"{i}{j}{k}"
            line += 1

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-e")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-Af", "stage=='build' and digit==0")
print("-----", flush=True)
