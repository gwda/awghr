# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running multiple workflows in the same directory."""

import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow, WorkflowException
from awghr._manage import cli as manage_cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

# -- Test multiple workflows in the same directory

ws = [Workflow(name="multi1") for n in range(2)]

for i in range(10):
    w = ws[i % 2]
    w.add_job(
        args=dict(index=i),
        cmds=["echo {index} > {_out}"],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        outputs=["out_{index}.txt"],
    )

for w in ws:
    w.write()

logger.info("test command line interface")
print("-----", flush=True)
manage_cli("-Pw", workflow_dir=w.workdir)
print("-----", flush=True)

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "await", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
for w in ws:
    statefile = w.workdir / "workflows" / w.ID / "state"
    assert statefile.is_file()
    submittedfile = w.workdir / "workflows" / w.ID / "submitted"
    assert not submittedfile.is_file()

logger.info("check workflow state is SUCCESS")
for w in ws:
    statefile = w.workdir / "workflows" / w.ID / "state"
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

logger.info("check that state is re-created")
for w in ws:
    statefile = w.workdir / "workflows" / w.ID / "state"
    statefile.unlink()
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "await"], check=True)
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
joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
assert joblistfile.is_file()
for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
    assert (jobdir / "job_state").is_file()

# -- Test multiple workflows duplicating the same jobs

ws = [Workflow(name="multi2") for n in range(2)]

for w, (i0, n) in zip(ws, ((0, 10), (5, 10))):
    for i in range(i0, n):
        w.add_job(
            args=dict(index=i),
            cmds=["echo {index} > {_out}"],
            time=(10, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
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

ws = [Workflow(name="multi3") for n in range(3)]

for w, (i0, n) in zip(ws, ((0, 5), (5, 10), (0, 10))):
    for i in range(i0, n):
        w.add_job(
            args=dict(index=i, i0=i0, n=n),
            cmds=["echo {index} > {_out}"],
            time=(10, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
            outputs=["out_{index}.txt"],
        )

ws[0].write()
ws[1].write()

try:
    ws[2].write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# -- Test workflows replacing previous workflows with same jobs/outputs

ws = [Workflow(name="multi4") for n in range(3)]

for w, (i0, n) in zip(ws, ((0, 5), (5, 10), (0, 10))):
    for i in range(i0, n):
        w.add_job(
            args=dict(index=i),
            cmds=["echo {index} > {_out}"],
            time=(10, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
            outputs=["out_{index}.txt"],
        )

for w in ws:
    w.write()

# -- Test workflows replacing previous workflows with same jobs/outputs

ws = [Workflow(name="multi5") for n in range(2)]

for w, (i0, n) in zip(ws, ((0, 5), (0, 10))):
    for i in range(i0, n):
        w.add_job(
            args=dict(index=i),
            cmds=["echo {index} > {_out}"],
            time=(10, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
            outputs=["out_{index}.txt"],
        )

for w in ws:
    w.write()
