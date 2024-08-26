import logging
import subprocess
import sys
import time
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

w = Workflow(name="cancel")

w.add_job(
    args=dict(sleep=1000),
    cmds=["bash -c 'sleep {sleep}; echo DONE > {_out}'"],
    time=1 * w.HOUR,
    mem=10 * w.MB,
    disk=10 * w.MB,
    outputs=["DONE.txt"],
)

w.write()

logger.info("submit workflow, wait for it to start running, then cancel it")
subprocess.run([w.workdir / "submit"], check=True)
joblistfile = w.workdir / f"workflow_jobs_{w.ID}.txt"
jobstatefile = Path(joblistfile.read_text().strip()) / "job_state"
n = 0
while not jobstatefile.is_file():
    logger.info("waiting for %s to exist ...", jobstatefile)
    time.sleep(2)
    n += 1
    assert n < 100
subprocess.run([w.workdir / "cancel"], check=True)
statefile = w.workdir / f"workflow_state_{w.ID}.txt"
n = 0
while not statefile.is_file():
    logger.info("waiting for %s to exist ...", statefile)
    time.sleep(2)
    n += 1
    assert n < 100

logger.info("check for files")
assert len(list(w.workdir.glob("workflow_state_*.txt"))) == 1
assert len(list(w.workdir.glob(".workflow_submitted_*.txt"))) == 0

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
