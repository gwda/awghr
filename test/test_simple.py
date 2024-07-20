import logging
import os
import subprocess
import sys
from pathlib import Path

from awghr import Workflow

# set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

w = Workflow(name="simple")

infile = Path.cwd() / "prefix.txt"
infile.write_text("XYZ")

# add 10 independent jobs
for i in range(10):
    w.add_job(
        args=dict(infile=str(infile), index=i),
        cmds=[["bash", "-c", "echo `cat {infile}`{index} > out_{index}.txt"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        inputs=[infile],
        outputs=["out_{index}.txt"],
    )

workdir = w.write()

if os.environ["BATCH_SUBMISSION"] == "no":
    sys.exit(0)

logger.info("submit workflow and wait for it to finish")
subprocess.run([workdir / "submit"], check=True)
subprocess.run([workdir / "wait", "-n", "1", workdir / "queue"], check=True)

logger.info("check for files")
assert len(list(workdir.glob("workflow_jobs_*.txt"))) == 1
assert len(list(workdir.glob("workflow_state_*.txt"))) == 1

logger.info("check state")
statefile = list(workdir.glob("workflow_state_*.txt"))[0]
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("check that state is re-created")
statefile.unlink()
subprocess.run([workdir / "submit"], check=True)
subprocess.run([workdir / "wait"], check=True)
state = statefile.read_text().strip()
assert state == "SUCCESS"

for i in range(10):
    outfile = workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == f"XYZ{i}"
