# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test workflow generation."""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from awghr import Workflow, WorkflowException

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

# Create workflow
w = Workflow(name="add_job")

# Try creating a workflow with a non-existent base directory
try:
    Workflow(name="dummy", basedir=Path("/nonexistent"))
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Add job
job_batchopt: Dict[str, Any] = dict()
job_args = dict(
    args=dict(index=0),
    cmds=["/usr/bin/true"],
    time=1 * w.MIN,
    mem=10 * w.MB,
    disk=10 * w.MB,
    ncpus=2,
    ngpus=1,
    inputs=[Path("/usr/bin/true")],
    outputs=["out_{index}.txt"],
    batchopt=job_batchopt,
)
w.add_job(**job_args)

# Try to add a duplicate job
try:
    w.add_job(**job_args)
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with argument names beginning with an underscore
try:
    w.add_job(
        args=dict(_not_allowed=True),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-existent executable
try:
    w.add_job(
        args=dict(index=999),
        cmds=["./does-not-exist"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-unique inputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[Path("./dummy.txt"), Path("./dummy.txt")],
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-existent inputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[Path("./non-existent.txt")],
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with an empty file list substitution
try:
    w.add_job(
        args=dict(index=999),
        cmds=["echo {_in}"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass
try:
    w.add_job(
        args=dict(index=999),
        cmds=["echo {_in[false]}"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[Path("/usr/bin/true")],
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-existent dependency
try:
    w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        inputs=[("non-existent-job", "dummy")],
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass
try:
    w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        needs=["non-existent-job"],
        outputs=["dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-unique outputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=1 * w.MIN,
        mem=10 * w.MB,
        disk=10 * w.MB,
        outputs=["dummy.txt", "dummy.txt"],
        batchopt=job_batchopt,
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Try to add a job with non-workflow-unique final output file
bad_job = w.add_job(
    args=dict(index=999),
    cmds=["true"],
    time=1 * w.MIN,
    mem=10 * w.MB,
    disk=10 * w.MB,
    outputs=["out_0.txt"],
    batchopt=job_batchopt,
)
try:
    w.write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass
finally:
    del w._jobs[bad_job]

# Write workflow
w.write()

# Test that workflow can be rewritten
w.write()

# Test that workflow cannot be rewritten once submitted
try:
    submittedfile = w.workdir / "batch" / w.ID / "workflow_submitted"
    submittedfile.touch()
    w.write()
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass
finally:
    submittedfile.unlink()
