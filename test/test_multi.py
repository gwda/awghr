import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow, WorkflowException
from awghr.cli import cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

# -- Test multiple workflows in the same directory

ws = [Workflow(name="multi1") for n in range(2)]

for i in range(10):
    w = ws[i % 2]
    w.add_job(
        args=dict(index=i),
        cmds=["bash -c 'echo {index} > {_out}'"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["out_{index}.txt"],
    )

for w in ws:
    w.write()

logger.info("test command line interface")
print("-----", flush=True)
cli("-d", str(w.workdir), "-Pb")
print("-----", flush=True)

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
for w in ws:
    statefile = w.workdir / "batch" / w.ID / "workflow_state"
    assert statefile.is_file()
    submittedfile = w.workdir / "batch" / w.ID / "workflow_submitted"
    assert not submittedfile.is_file()

logger.info("check workflow state is SUCCESS")
for w in ws:
    statefile = w.workdir / "batch" / w.ID / "workflow_state"
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

logger.info("check that state is re-created")
for w in ws:
    statefile = w.workdir / "batch" / w.ID / "workflow_state"
    statefile.unlink()
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait"], check=True)
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

for i in range(10):
    outfile = w.workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == str(i)

logger.info("check that re-writing workflow does not remove successful states")
for w in ws:
    w.write()
joblistfile = w.workdir / "batch" / w.ID / "workflow_all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert (jobdir / "job_state").is_file()

# -- Test multiple workflows duplicating the same jobs

ws = [Workflow(name="multi2") for n in range(2)]

for w, n in zip(ws, (5, 10)):
    for i in range(n):
        w.add_job(
            args=dict(index=i),
            cmds=["bash -c 'echo {index} > {_out}'"],
            time=1 * w.MIN,
            mem=10 * w.MB,
            disk=10 * w.MB,
            outputs=["out_{index}.txt"],
        )

ws[0].write()

try:
    ws[1].write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# -- Test multiple workflows duplicating the same outputs

ws = [Workflow(name="multi3") for n in range(2)]

for w, i0 in zip(ws, (0, 5)):
    for i in range(i0, i0 + 10):
        w.add_job(
            args=dict(index=i),
            cmds=["bash -c 'echo {index} > {_out}'"],
            time=1 * w.MIN,
            mem=10 * w.MB,
            disk=10 * w.MB,
            outputs=["out_{index}.txt"],
        )

ws[0].write()

try:
    ws[1].write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

ws = [Workflow(name="multi4") for n in range(2)]

for w, n in zip(ws, (5, 10)):
    for i in range(n):
        w.add_job(
            args=dict(index=i, n=n),
            cmds=["bash -c 'echo {index} > {_out}'"],
            time=1 * w.MIN,
            mem=10 * w.MB,
            disk=10 * w.MB,
            outputs=["out_{index}.txt"],
        )

for w in ws:
    w.write()
