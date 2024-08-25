import logging
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

w = Workflow(name="simple")

infile = Path.cwd() / "prefix.txt"
infile.write_text("XYZ")

# Add 10 independent jobs
for i in range(10):
    w.add_job(
        args=dict(index=i, const=10),
        cmds=[
            """
            bash -c '
                if [ $MYTHREADS -ne 1 ]; then
                    echo 'MYTHREADS!=1' >&2;
                    exit 1;
                fi;
                echo $HOME;
                echo ${{TESTVAR}}1 >&2;
                echo ${{TESTVAR}}2 >&2;
                echo ${{TESTVAR}}3 >&2;
                echo xyz >&2;
                echo `cat {_in["prefix"]}`{index} > out_{index}.txt
            '
            """
        ],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[infile],
        outputs=["out_{index}.txt"],
    )

w.write()

logger.info("check for job files that should (not) exist before submission")
joblistfile = w.workdir / f"workflow_jobs_{w.ID}.txt"
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

logger.info("check for files")
assert len(list(w.workdir.glob("workflow_jobs_*.txt"))) == 1
assert len(list(w.workdir.glob("workflow_state_*.txt"))) == 1
assert len(list(w.workdir.glob(".workflow_submitted_*.txt"))) == 0

logger.info("check workflow state is SUCCESS")
statefile = w.workdir / f"workflow_state_{w.ID}.txt"
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check that state is re-created")
statefile.unlink()
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait"], check=True)
state = statefile.read_text().strip()
assert state == "SUCCESS"

for i in range(10):
    outfile = w.workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == f"XYZ{i}"

logger.info("check for job files that should exist after submission")
joblistfile = w.workdir / f"workflow_jobs_{w.ID}.txt"
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert (jobdir / "job_batchID").is_file()
    assert (jobdir / "job_state").is_file()
    assert (jobdir / "job_args.json").is_file()
    assert (jobdir / "job_script.sh").is_file()

logger.info("check that job standard output/error printed what was expected")
joblistfile = w.workdir / f"workflow_jobs_{w.ID}.txt"
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    stdout = (jobdir / "job_stdout").read_text().strip()
    stderr = (jobdir / "job_stderr").read_text().strip()
    assert stdout == str(w.workdir.parent)
    assert stderr == "\n".join(["abc1", "abc2", "abc3", "xyz"])

logger.info("test command line interface")
print("-----", flush=True)
cli("jobs", "-d", str(w.workdir), "-Ae", "-n", "1", "-g", "a")
print("-----", flush=True)
cli("-d", str(w.workdir), "-f", "index < 5", "-Ajlon", "0")
print("-----", flush=True)
try:
    cli("jobs", "-d", str(w.workdir), "-n", "-1")
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
