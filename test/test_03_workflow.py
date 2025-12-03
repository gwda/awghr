# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test workflow generation."""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from awghr import Workflow, WorkflowException
from awghr._config import batch_system_names

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

# Batch system specific accounts
accounts = {"local": "user@local_account"}
for batch_system in batch_system_names():
    accounts[batch_system] = "user@" + batch_system + "_account"

# Create workflow
w = Workflow(name="add_job", account=accounts)
assert w.account == "user@" + w.batch_system + "_account"

# Try creating a workflow with no account
try:
    os.environ["AWGHR_CFGFILE"] = str(Path.cwd() / ".awghr-no-account.cfg")
    Workflow(name="dummy", account=None)
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass
finally:
    del os.environ["AWGHR_CFGFILE"]

# Try creating a workflow with a non-existent base directory
try:
    Workflow(name="dummy", basedir=Path("/nonexistent"))
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

# Add job
job_batchopt: Dict[str, Any] = dict(
    htcondor=dict(sub=dict(condor_test_opt="anotherval")),
    slurm=dict(comment="a comment with spaces"),
)
job_args: Dict[str, Any] = dict(
    args=dict(index=0),
    cmds=["/usr/bin/true"],
    time=(1, "min"),
    mem=(10, "MB"),
    disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
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
    time=(1, "min"),
    mem=(10, "MB"),
    disk=(10, "MB"),
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

# Try to add a job with options for an unknown batch system
try:
    bad_job = w.add_job(
        args=dict(index=999),
        cmds=["true"],
        time=(1, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        outputs=["out_0.txt"],
        batchopt=dict(dummy=dict(k=1)),
    )
    msg = "should have raised a WorkflowException"
    raise AssertionError(msg)
except WorkflowException:
    pass

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

# Batch system specific options

if w.batch_system == "htcondor":  # HTCondor

    # Try to add a job with a duplicate submit file option
    try:
        bad_job = w.add_job(
            args=dict(index=999),
            cmds=["true"],
            time=(1, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
            outputs=["dummy.txt"],
            batchopt=dict(htcondor=dict(sub=dict(executable="dummy"))),
        )
        w.write()
        msg = "should have raised a WorkflowException"
        raise AssertionError(msg)
    except WorkflowException:
        pass
    finally:
        del w._jobs[bad_job]

    # Check that submit file options are written out
    for sub_opt, sub_val in job_batchopt["htcondor"]["sub"].items():
        sub_opt_str = "{} = {}".format(sub_opt, sub_val).lower()
        sub_opt_found = False
        for root, dirs, files in os.walk(w.workdir):
            for fname in files:
                if fname == "job_condor.sub":
                    for line in (Path(root) / fname).open("r"):
                        if sub_opt_str in line.lower():
                            sub_opt_found = True
        assert sub_opt_found
