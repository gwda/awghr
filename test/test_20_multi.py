# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running multiple workflows in the same directory."""

import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow, WorkflowException
from awghr import _cli as workflow_cli
from awghr._manage import cli as manage_cli
from awghr._util import job_dir_from_ID

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

# -- Test multiple workflows duplicating the same jobs

ws = [Workflow(name="multi_dup_job") for n in range(2)]

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

ws = [Workflow(name="multi_dup_out") for n in range(3)]

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

ws = [Workflow(name="multi_repl1") for n in range(3)]

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

ws = [Workflow(name="multi_repl2") for n in range(2)]

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

# -- Test multiple workflows in the same directory, submitted concurrently

ws = [Workflow(name="multi_con") for n in range(2)]

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
assert manage_cli("-Pw", workdir=w.workdir) == 0
print("-----", flush=True)

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

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
    subprocess.run([w.workdir / "wait"], check=True)
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

for i in range(10):
    outfile = w.workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == str(i)

logger.info("check that successful workflow cannot be rewritten")
try:
    w.write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

for j in range(2):
    logger.info("test archiving workflows (#%i)", j + 1)
    assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
    assert not (w.workdir / "jobs").is_dir()
    for w in ws:
        archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
        assert archive_tarfile.is_file()

# -- Test multiple workflows in the same directory, submitted sequentially

ws = [Workflow(name="multi_seq") for n in range(2)]

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


i = -1
while True:

    if i >= 0:
        for j in range(2):
            logger.info("test archiving workflows (#%i)", j + 1)
            assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
            archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
            assert archive_tarfile.is_file()

    i += 1
    if i >= 2:
        break
    w = ws[i]

    w.write()

    logger.info("test command line interface")
    print("-----", flush=True)
    manage_cli("-Pw", workdir=w.workdir)
    print("-----", flush=True)

    logger.info("submit workflow and wait for it to finish")
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

    logger.info("check for workflow files")
    statefile = w.workdir / "workflows" / w.ID / "state"
    assert statefile.is_file()
    submittedfile = w.workdir / "workflows" / w.ID / "submitted"
    assert not submittedfile.is_file()

    logger.info("check workflow state is SUCCESS")
    statefile = w.workdir / "workflows" / w.ID / "state"
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

    logger.info("check that state is re-created")
    statefile = w.workdir / "workflows" / w.ID / "state"
    statefile.unlink()
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait"], check=True)
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

    logger.info("check that successful workflow cannot be rewritten")
    try:
        w.write()
        msg = "should have raised a WorkflowException"
        raise AssertionError(msg)
    except WorkflowException:
        pass

for i in range(10):
    outfile = w.workdir / "outputs" / f"out_{i}.txt"
    logger.info("check that output file '%s' is present and correct", outfile)
    assert outfile.is_file()
    outfile.read_text() == str(i)

# -- Test workflows sharing jobs

ws = [Workflow(name="multi_share") for n in range(3)]

for w in ws:
    setup_id = w.add_job(
        args=dict(stage="setup"),
        cmds=["date > setup.txt"],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        outputs=["setup.txt"],
    )

setup_job_dir = job_dir_from_ID(w.workdir / "jobs", setup_id)
setup_date_0 = None

for i, w in enumerate(ws[0:2]):
    w.add_job(
        args=dict(stage="use", index=i),
        cmds=[
            """
            echo "date=`cat setup.txt` job={index}" > out_{index}.txt
            """
        ],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        inputs=[(setup_id, "setup.txt")],
        outputs=["out_{index}.txt"],
    )

    w.write()

    logger.info("submit workflow and wait for it to finish")
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

    logger.info("check for workflow files")
    statefile = w.workdir / "workflows" / w.ID / "state"
    assert statefile.is_file()
    submittedfile = w.workdir / "workflows" / w.ID / "submitted"
    assert not submittedfile.is_file()

    logger.info("check workflow state is SUCCESS")
    statefile = w.workdir / "workflows" / w.ID / "state"
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

    if setup_date_0 is None:

        logger.info("get setup date written by first workflow")
        setup_date_0 = (setup_job_dir / "setup.txt").read_text().strip()

    else:

        logger.info("check that setup date has not been changed")
        setup_date = (setup_job_dir / "setup.txt").read_text().strip()
        assert setup_date == setup_date_0

for j in range(2):
    logger.info("test archiving workflows (#%i)", j + 1)
    assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
    for w in ws[0:2]:
        archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
        assert archive_tarfile.is_file()
    assert list(w.workdir.glob("**/job_state")) == [setup_job_dir / "job_state"]

w = ws[2]

w.write()

logger.info("submit workflow and wait for it to finish")
subprocess.run([w.workdir / "submit"], check=True)
subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

logger.info("check for workflow files")
statefile = w.workdir / "workflows" / w.ID / "state"
assert statefile.is_file()
submittedfile = w.workdir / "workflows" / w.ID / "submitted"
assert not submittedfile.is_file()

logger.info("check workflow state is SUCCESS")
statefile = w.workdir / "workflows" / w.ID / "state"
state = statefile.read_text().strip()
assert state == "SUCCESS"

logger.info("test archiving workflow with only jobs used by other workflows")
assert workflow_cli("archive-workflows", workdir=w.workdir) == 0
archive_tarfile = w.workdir / "workflows" / f"{w.ID}.tar.bz2"
assert not archive_tarfile.is_file()
assert list(w.workdir.glob("**/job_state")) == [setup_job_dir / "job_state"]
