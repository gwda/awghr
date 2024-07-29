import logging
import os
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
logger = logging.getLogger(__name__)

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


w = Workflow(name="dag_fail", dated=False, tags=["maxarray"])

# Build up files containing up to 3 digits
for level in range(3):
    if level == 0:
        jobs = []
        for digit in range(3):
            job_ID = w.add_job(
                args=dict(stage="build", digit=digit),
                cmds=[["bash", "-c", "echo {digit} > {_out[0]}"]],
                time=1 * w.MIN,
                mem=10 * w.MiB,
                disk=10 * w.MiB,
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
                        [
                            "bash",
                            "-c",
                            get_fail_cmd("echo `cat {_in[0]}`{digit} > {_out[0]}"),
                        ]
                    ],
                    time=1 * w.MIN,
                    mem=10 * w.MiB,
                    disk=10 * w.MiB,
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
                    [
                        "bash",
                        "-c",
                        get_fail_cmd("cat {_in[0]} {_in[1]} | sort > {_out[0]}"),
                    ]
                ],
                time=1 * w.MIN,
                mem=10 * w.MiB,
                disk=10 * w.MiB,
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
                    [
                        "bash",
                        "-c",
                        "cat {_in[0]} > {_out[0]}",
                    ]
                ],
                time=1 * w.MIN,
                mem=10 * w.MiB,
                disk=10 * w.MiB,
                inputs=[(prev_job_ID, "f-{_id}.txt")],
                outputs=["f-{_id}.txt"],
            )
            jobs.append(job_ID)


workdir = w.write()

if os.environ["BATCH_SUBMISSION"] == "no":
    sys.exit(0)

while True:
    logger.info(
        "submit workflow and wait for it to finish (%i fail files exist)",
        len(fail_files),
    )
    subprocess.run([workdir / "submit"], check=True)
    subprocess.run([workdir / "wait", workdir / "queue"], check=True)

    logger.info("check for files (%i fail files exist)", len(fail_files))
    assert len(list(workdir.glob("workflow_jobs_*.txt"))) == 1
    assert len(list(workdir.glob("workflow_state_*.txt"))) == 1
    if len(fail_files) > 0:
        assert len(list(workdir.glob("workflow_failed_jobs_*.txt"))) == 1

    expected_state = "FAILED" if len(fail_files) > 0 else "SUCCESS"
    logger.info(
        "check state is %s (%i fail files exist)", expected_state, len(fail_files)
    )
    statefile = list(workdir.glob("workflow_state_*.txt"))[0]
    state = statefile.read_text().strip()
    assert state == expected_state

    # Successively remove fail files so that jobs can pass
    if len(fail_files) == 0:
        break
    fail_file = fail_files.pop(0)
    fail_file.unlink()

outfile = workdir / "outputs" / f"f-{jobs[0]}.txt"
logger.info("check that output file '%s' is present and correct", outfile)
assert outfile.is_file()
digits = outfile.read_text().splitlines()
line = 0
for i in range(3):
    for j in range(3):
        for k in range(3):
            assert digits[line] == f"{i}{j}{k}"
            line += 1
