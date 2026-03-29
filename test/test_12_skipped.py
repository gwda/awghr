# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test running a workflow where some jobs are skipped."""

import json
import logging
import subprocess
import sys
from pathlib import Path

from awghr import Workflow
from awghr._manage import cli as manage_cli

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(Path(__file__).name)

# Test different skip exit codes
for skip_exit_code in (77, 99):

    # -- Test that independent jobs can be skipped

    w = Workflow(f"skipped-{skip_exit_code}-1", skip_exit_code=skip_exit_code)

    # Add jobs
    for exit_code in (0, skip_exit_code):
        w.add_job(
            args=dict(exit_code=exit_code),
            cmds=[
                (
                    "echo DONE > file{exit_code}.txt"
                    if exit_code == 0
                    else "exit {exit_code}"
                )
            ],
            time=(10, "min"),
            mem=(10, "MB"),
            disk=(10, "MB"),
            outputs=["file{exit_code}.txt"],
            batchopt=dict(htcondor={"dag_retry": 1}),
        )

    w.write()

    logger.info("submit workflow and wait for it to finish")
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

    logger.info("check workflow state is SUCCESS")
    statefile = w.workdir / "workflows" / w.ID / "state"
    assert statefile.is_file()
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

    logger.info("check job states are as expected")
    joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
    assert joblistfile.is_file()
    for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
        jobargs = json.load((jobdir / "job_args.json").open())
        jobstate = (jobdir / "job_state").read_text().strip()
        if jobargs["exit_code"] == skip_exit_code:
            assert jobstate == "SUCCESS/SKIPPED"
            assert not (jobdir / f"file{skip_exit_code}.txt").is_file()
        else:
            assert jobstate == "SUCCESS"
            assert (jobdir / "file0.txt").is_file()

    logger.info("check that output file were (not) created as expected")
    outfiledir = w.workdir / "outputs"
    assert (outfiledir / "file0.txt").is_file()
    assert not (outfiledir / f"file{skip_exit_code}.txt").is_file()

    logger.info("test command line interface")
    print("-----", flush=True)
    assert manage_cli("-A", workdir=w.workdir) == 0
    print("-----", flush=True)

    # -- Test that dependent jobs cannot be skipped

    w = Workflow(f"skipped-{skip_exit_code}-2", skip_exit_code=skip_exit_code)

    # Add jobs
    j1 = w.add_job(
        args=dict(exit_code=skip_exit_code),
        cmds=[
            "echo DONE > file{exit_code}.txt; exit {exit_code}",
        ],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        outputs=["file{exit_code}.txt"],
    )
    w.add_job(
        args=dict(exit_code=0),
        cmds=[
            "echo DONE > file{exit_code}.txt; exit {exit_code}",
        ],
        time=(10, "min"),
        mem=(10, "MB"),
        disk=(10, "MB"),
        needs=[j1],
        outputs=["file{exit_code}.txt"],
    )

    w.write()

    logger.info("submit workflow and wait for it to finish")
    subprocess.run([w.workdir / "submit"], check=True)
    subprocess.run([w.workdir / "wait", "-n", "1", w.workdir / "queue"], check=True)

    logger.info("check workflow state is SUCCESS")
    statefile = w.workdir / "workflows" / w.ID / "state"
    assert statefile.is_file()
    state = statefile.read_text().strip()
    assert state == "SUCCESS"

    logger.info("check job states are as expected")
    joblistfile = w.workdir / "workflows" / w.ID / "all_jobs"
    assert joblistfile.is_file()
    for jobdir in [Path(line) for line in joblistfile.read_text().split()]:
        jobstate = (jobdir / "job_state").read_text().strip()
        assert jobstate == "SUCCESS/SKIPPED"

    logger.info("check that output file were not created as expected")
    outfiledir = w.workdir / "outputs"
    assert not (outfiledir / "file0.txt").is_file()
    assert not (outfiledir / f"file{skip_exit_code}.txt").is_file()

    logger.info("test command line interface")
    print("-----", flush=True)
    assert manage_cli("-A", workdir=w.workdir) == 0
    print("-----", flush=True)
