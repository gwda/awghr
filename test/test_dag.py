import logging
import subprocess
import sys

from awghr import Workflow

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

w = Workflow(name="dag", tags=["maxarray"])

# Build up files containing up to 3 digits
for level in range(3):
    if level == 0:
        jobs = []
        for digit in range(3):
            job_ID = w.add_job(
                args=dict(stage="build", digit=digit),
                cmds=["bash -c 'echo {digit} > {_out}'"],
                time=1 * w.MIN,
                mem=10 * w.MB,
                disk=10 * w.MB,
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
                        "bash -c 'echo `cat {_in}`{digit} > {_out}'",
                    ],
                    time=1 * w.MIN,
                    mem=10 * w.MB,
                    disk=10 * w.MB,
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
                    "bash -c 'cat {_in} | sort > {_out}'",
                ],
                time=1 * w.MIN,
                mem=10 * w.MB,
                disk=10 * w.MB,
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
                    "bash -c 'cat {_in} > {_out}'",
                ],
                time=1 * w.MIN,
                mem=10 * w.MB,
                disk=10 * w.MB,
                inputs=[(prev_job_ID, "f-{_id}.txt")],
                outputs=["f-{_id}.txt"],
            )
            jobs.append(job_ID)


w.write()

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", w.workdir / "queue"], check=True)

logger.info("check for files")
assert len(list(w.workdir.glob("workflow_jobs_*.txt"))) == 1
assert len(list(w.workdir.glob("workflow_state_*.txt"))) == 1

logger.info("check workflow state is SUCCESS")
statefile = w.workdir / f"workflow_state_{w.ID}.txt"
state = statefile.read_text().strip()
assert state == "SUCCESS"

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
