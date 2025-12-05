# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""A(nother) Workflow Generator for High-throughput computeRing."""

import json
import logging
import math
import os
import re
import shlex
import shutil
import sys
from hashlib import blake2b
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pint import Quantity

from ._config import batch_system_names, cluster_names, read_config
from ._util import (
    FileLineWriter,
    JobFileList,
    ProgressLogger,
    WorkflowException,
    job_dir_from_ID,
)

__author__ = "Karl Wette"
__version__ = "0.1"

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
_logger = logging.getLogger(__name__)


class Workflow:
    """Generate a workflow.

    Attributes:
        name (str):
            Name of workflow.

        workdir (Path):
            Working directory where workflow will be written.

        cluster (str):
            Name of the computer cluster where the workflow is being generated.
            Cluster names are defined by the user by matching against batch system,
            host name, etc. in a configuration section in :literal:`~/.awghr.cfg`.

        batch_system (str):
            Batch system of the computer cluster where the workflow is being generated.

        account (str):
            Account the workflow should be charged against.
            If not given, use the default value given in :literal:`~/.awghr.cfg`.

        ID (str):
            A unique string representing the workflow.
            Only available after ``write()`` has been called.

    Configuration:
        Default workflow configurations are retrieved from :literal:`~/.awghr.cfg`.

        This is a ``ConfigParser``-style configuration file with the following syntax::

            [DEFAULT]           # This section match all batch systems/hosts/tags
            account = ...           # Default value of ``account`` argument passed to Workflow()
            basedir = ...           # Default value of ``basedir`` argument passed to Workflow()
            envlist =               # List of environment variables to save/set in job script:
                *PATH               # - Save any variables matching this regular expression.
                VAR=val             # - Set variable to the given value.
                ...                 #   May contain ``{format}`` strings which will be filled by:
                                    #   * ``_time_min``: requested wall time in minutes.
                                    #   * ``_mem_MB``: requested memory in MB.
                                    #   * ``_disk_MB``: requested temporary disk space in MB.
                                    #   * ``_ncpus``: requested number of CPUs.
                                    #   * ``_ngpus``: requested number of GPUs.

            # Section headings are arbitrary
            [host matching]     # This section matches the host ``login1.example.com``
            match host = login1.example.com
            match host = .example.com
            match host = login1
            match host = login
            cluster = cluster1
            ...

            [batch matching]    # This section matches a given batch system
            match batch = ...
            # Batch system matching sections may include system-specific keys

            [tag matching]      # This section matches a tag in the ``tags`` list passed to Workflow()
            match tag = ...
            ...

    """

    # Attributes
    name: str
    workdir: Path
    cluster: str
    batch_system: str
    account: str
    ID: str = "0" * 16

    def __init__(
        self,
        name: str,
        *,
        tags: Optional[List[str]] = None,
        basedir: Optional[Path] = None,
        account: Optional[Union[str, Dict[str, str]]] = None,
    ):
        """Create a new workflow.

        Args:
            name (str):
                Name of workflow.

            tags (Optional[List[str]]):
                List of tags, used to retrieve default arguments from :literal:`~/.awghr.cfg`.

            basedir (Optional[Path]):
                Base workflow directory; this workflow will be written to subdirectory.
                If not given, use the default value given in :literal:`~/.awghr.cfg`,
                or else the current directory.

            account (Optional[Union[str, Dict[str, str]]]):
                Account the workflow should be charged against.
                If a dictionary, lists different accounts for different batch systems; the
                batch system of the computer cluster where the workflow is being generated
                is used to select the correct account.
                If not given, use the default value given in :literal:`~/.awghr.cfg`;
                if no default value is defined, an error is raised.
        """

        # Read configuration
        _logger.info("reading configuration")
        cfg = read_config(tags=tags)

        # Check base directory
        if basedir is None:
            basedir = cfg["basedir"]
        try:
            basedir = basedir.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            msg = f"base directory '{basedir}' is not a directory"
            raise WorkflowException(msg)

        # Set name and working directory
        self.name = name
        self.workdir = basedir / self.name

        # Set name of cluster
        self.cluster = cfg["cluster"]

        # Set batch system
        self.batch_system = cfg["batch_system"]

        # Set account
        if isinstance(account, dict):
            self.account = account[self.batch_system]
        elif account is None:
            self.account = cfg["account"]
        else:
            self.account = account
        if self.account is None:
            msg = "no account has been defined"
            raise WorkflowException(msg)

        # Set internal variables
        self._workflow_writer = cfg["workflow_writer"](
            batchsyscfg=cfg["batch_system_cfg"]
        )
        self._workflow_hash = blake2b(digest_size=8, usedforsecurity=False)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._log_add_jobs = ProgressLogger(
            logger=_logger, msg="added %s jobs to workflow"
        )

        # Process environment variables
        self._env = {}
        if cfg["envlist"] is not None:
            for env in cfg["envlist"]:
                if "=" not in env:
                    for var in os.environ:
                        if re.fullmatch(env, var) is not None:
                            self._env[var] = os.environ[var]
            for env in cfg["envlist"]:
                if "=" in env:
                    var, val = (s.strip() for s in env.split("=", maxsplit=1))
                    self._env[var] = val

        # Log details
        _logger.info("workflow name: %s", self.name)
        _logger.info("workflow account: %s", self.account)
        _logger.info("workflow directory: %s", self.workdir)
        _logger.info("batch system: %s", self.batch_system)

    def add_job(
        self,
        *,
        args: Dict[str, Any],
        cmds: List[Union[str, List[Any]]],
        time: Tuple[float, str],
        mem: Tuple[float, str],
        disk: Tuple[float, str],
        ncpus: int = 1,
        ngpus: int = 0,
        needs: Optional[List[str]] = None,
        inputs: Optional[List[Union[Path, Tuple[str, str]]]] = None,
        outputs: List[str],
        batchopt: Optional[Dict[str, Dict[str, Any]]] = None,
        final: Optional[bool] = None,
    ) -> str:
        """Add a new job to the workflow.

        Args:
            args (Dict[str, Any]):
                Arguments and their values which uniquely identify the job.
                Argument names cannot begin with an underscore.

            cmds (List[Union[str, List[Any]]]):
                A list of commands to execute.

                Commands may be:

                * strings, which are treated as ``bash`` scripts; or
                * lists, which are treated as arguments of a single command.
                  List arguments may be:

                  * dictionaries, which are formatted as a series of arguments
                    ``key=value``, where ``key``/``value`` are its key/values;
                  * paths, which are staged as additional input files (see ``inputs``
                    below), and the argument replaced with a job-local path to the file;
                  * anything else are formatted as a string.

                Commands may contain ``{format}`` strings which will be filled by:

                * the dictionary supplied in ``args``;
                * ``_in``: input file(s) supplied in ``inputs``, separated by spaces.
                * ``_in[re:sep]``: the input file(s) supplied in ``inputs`` that
                  match the regular expression ``re``, separated by ``sep``.
                  Both arguments are optional.
                  Example: ``_in[\.dat$:,]`` returns file(s) with ``.dat`` extensions,
                  separated by commas.
                * ``_out``: output file(s) supplied in ``outputs``, separated by spaces.
                * ``_out[re:sep]``: the output file(s) supplied in ``outputs`` that
                  match the regular expression ``re``, separated by ``sep``.
                  See ``_in[...]``.
                * ``_time_min``: requested wall time in minutes.
                * ``_mem_MB``: requested memory in MB.
                * ``_disk_MB``: requested temporary disk space in MB.
                * ``_ncpus``: requested number of CPUs.
                * ``_ngpus``: requested number of GPUs.

            time (Tuple[float, str]):
                Requested wall time of the job: ``(magnitude, "unit")``

            mem (Tuple[float, str]):
                Requested memory of the job: ``(magnitude, "unit")``

            disk (Tuple[float, str]):
                Requested temporary disk space of the job: ``(magnitude, "unit")``

            ncpus (int):
                Requested number of CPUs of the job.
                Default is 1.

            ngpus (int):
                Requested number of GPUs of the job.
                Default is 0.

            needs (Optional[List[str]]):
                A list of IDs of prerequisite jobs in the same workflow.

            inputs (Optional[List[Union[Path, Tuple[str, str]]]]):
                A list of input files, either:

                * a path to a file not generated by the workflow; or
                * a tuple (job ID, file name) representing a file generated
                  by a previous job in the same workflow.
                  May contain ``{format}`` strings which will be filled by:

                  * the dictionary supplied in ``args`` to the previous job;
                  * ``_id``: the unique string representing the previous job.

                Input files will be staged to the working directory of the job.

            outputs (List[str]):
                A list of output files generated by the job.
                May contain ``{format}`` strings which will be filled by:

                * the dictionary supplied in ``args``;
                * ``_id``: the unique string representing the job.

                Output files will be staged out of the working directory of the job.

            batchopt (Optional[Dict[str, Dict[str, Any]]]):
                Batch system-specific configuration options.
                These options are given as nested dictionaries:

                * The key of the first dictionary is the cluster name and/or batch
                  system the option applies to. The cluster name is defined by the
                  user in :literal:`~/.awghr.cfg`; the batch system is determined
                  automatically from of the computer cluster where the workflow is
                  being generated. If both a cluster name and batch system are
                  required, they are given (in any order) separated by an underscore.

                * The value of the dictionary is itself a dictionary of options
                  for the given cluster/batch system. Only options given for
                  this cluster/batch system combination are used; other options
                  are silently ignored.

            final (Optional[bool]):
                If true, output files from this job will be copied to the final
                workflow directory. If not given, all jobs which are not dependencies
                of other jobs will have their output files copied to this directory.

        Returns:
            str: a unique string representing the job.

        """

        # Check argument names
        underscore_args = [arg for arg in args.keys() if arg.startswith("_")]
        if underscore_args:
            msg = "argument names ('{}') cannot begin with an underscore".format(
                ", ".join(underscore_args)
            )
            raise WorkflowException(msg)

        # Generate JSON representation of arguments
        args_json = json.dumps(args, default=str, sort_keys=True)

        # Generate job ID as hash of JSON representation,
        # and update hash used to generate workflow ID
        job_hash = blake2b(digest_size=8, usedforsecurity=False)
        args_json_bytes = args_json.encode("utf-8")
        job_hash.update(args_json_bytes)
        self._workflow_hash.update(args_json_bytes)
        job_ID = job_hash.hexdigest()

        # Create job
        if job_ID in self._jobs:
            msg = f"job with arguments {args} already exists in workflow"
            raise WorkflowException(msg)
        job: Dict[str, Any] = {}
        job["ID"] = job_ID

        # Store job arguments
        job["args"] = args
        job["args_json"] = args_json

        # Normalise and store resource requests
        job["time_min"] = int(
            math.ceil(Quantity(time[0], time[1]).to("minute").magnitude)
        )
        job["mem_MB"] = int(
            math.ceil(Quantity(mem[0], mem[1]).to("megabyte").magnitude)
        )
        job["disk_MB"] = int(
            math.ceil(Quantity(disk[0], disk[1]).to("megabyte").magnitude)
        )
        job["ncpus"] = ncpus
        job["ngpus"] = ngpus
        job["resources"] = {
            f"_{k}": job[k] for k in {"time_min", "mem_MB", "disk_MB", "ncpus", "ngpus"}
        }

        # Create job directory
        jobdir = job_dir_from_ID(self.workdir / "jobs", job_ID)
        job["dir"] = jobdir

        # Parse input file list
        job["infiles"] = []
        need_job_IDs = []
        if inputs is not None:
            if len(inputs) != len(set(inputs)):
                msg = "'inputs' is not a unique list"
                raise WorkflowException(msg)
            for inp in inputs:
                if isinstance(inp, Path):
                    if not inp.is_file():
                        msg = f"external input file '{inp}' does not exist"
                        raise WorkflowException(msg)
                    job["infiles"].append(inp)
                else:
                    inp_job_ID, inp_filename = inp
                    if inp_job_ID not in self._jobs:
                        msg = f"job ID '{inp_job_ID}' for input file '{inp_filename}' does not exist"
                        raise WorkflowException(msg)
                    inp_file = self._jobs[inp_job_ID]["dir"] / inp_filename.format(
                        **self._jobs[inp_job_ID]["args"], _id=inp_job_ID
                    )
                    job["infiles"].append(inp_file)
                    need_job_IDs.append(inp_job_ID)

        # Parse job dependencies and dependency order
        job["needs"] = []
        job["order"] = 0
        needs_hash = blake2b(digest_size=8, usedforsecurity=False)
        if needs is not None:
            need_job_IDs.extend(needs)
        for need_job_ID in need_job_IDs:
            if need_job_ID not in self._jobs:
                msg = f"job ID '{need_job_ID}' does not exist"
                raise WorkflowException(msg)
            job["needs"].append(need_job_ID)
            job["order"] = max(job["order"], self._jobs[need_job_ID]["order"] + 1)
            needs_hash.update(self._jobs[need_job_ID]["args_json"].encode("utf-8"))
        job["needs_ID"] = needs_hash.hexdigest()

        # Parse output file list, and store whether job is considered final
        job["outfiles"] = []
        if outputs is not None:
            if len(outputs) != len(set(outputs)):
                msg = "'outputs' is not a unique list"
                raise WorkflowException(msg)
            for outp in outputs:
                job["outfiles"].append(jobdir / outp.format(**args, _id=job_ID))
        job["outfinal"] = final

        # Reolve all input and output file paths
        for key in ("infiles", "outfiles"):
            job[key] = [path.resolve() for path in job[key]]

        # Parse and store commands
        format_cmd = dict(args)
        format_cmd["_in"] = JobFileList("_in", [p.name for p in job["infiles"]])
        format_cmd["_out"] = JobFileList("_out", [p.name for p in job["outfiles"]])
        format_cmd.update(job["resources"])
        job["cmds"] = []
        for cmd in cmds:

            if isinstance(cmd, str):

                # Treat `cmd` as a bash script
                cmd = dedent(cmd).strip().format(**format_cmd)

            else:

                # Treat `cmd` as a single command
                cmd_args = []
                for arg in cmd:
                    if isinstance(arg, dict):

                        # Format dict argument as ``key=value``
                        cmd_args.extend(
                            [f"{k}={v}".format(**format_cmd) for k, v in arg.items()]
                        )

                    elif isinstance(arg, Path):

                        # Stage path in input files, format as local path
                        arg = arg.resolve()
                        if arg not in job["infiles"]:
                            job["infiles"].append(arg)
                        cmd_args.append("./" + arg.name)

                    else:

                        # Format all other arguments as strings
                        cmd_args.append(str(arg).format(**format_cmd))

                # Quote arguments
                cmd = " ".join(shlex.quote(arg) for arg in cmd_args)

            job["cmds"].append(cmd)

        # Add batch system-specific options
        job["batchopt"] = {}
        if batchopt:
            for cluster_batch_system_str, cluster_batch_system_opts in batchopt.items():
                cluster_batch_system = cluster_batch_system_str.split("_")
                for s in cluster_batch_system:
                    if s not in cluster_names and s not in batch_system_names():
                        msg = f"'{s}' does not match either a cluster name or a batch system name"
                        raise WorkflowException(msg)
                if (
                    self.cluster in cluster_batch_system
                    or self.batch_system in cluster_batch_system
                ):
                    for opt, val in cluster_batch_system_opts.items():
                        if opt in job["batchopt"]:
                            msg = f"conflicting values for batch system-specific option '{opt}'"
                            raise WorkflowException(msg)
                    job["batchopt"][opt] = val

        # Add job to workflow
        self._jobs[job_ID] = job

        # Log progress
        self._log_add_jobs.update()

        return job_ID

    def __getitem__(self, key: Tuple[str, str]) -> Any:
        """Retrieve the value of a job argument."""
        return self._jobs[key[0]]["args"][key[1]]

    def write(self) -> None:
        """Write workflow files and directories."""

        # Log progress
        self._log_add_jobs.finish()

        # Generate workflow ID as hash of all job JSON representations
        self.ID = self._workflow_hash.hexdigest()
        assert self.ID != "0" * 16

        # Directory containing batch system scripts
        basebatchdir = self.workdir / "batch"
        batchdir = basebatchdir / self.ID

        # Name of file indicating workflow has been submitted
        submittedfile = batchdir / "workflow_submitted"

        # Check that workflow has not been submitted
        if submittedfile.is_file():
            msg = f"workflow {self.ID} has already been submitted"
            raise WorkflowException(msg)

        # Determine intermediate jobs, which are required as dependencies
        intermediate_jobs = set()
        for job in self._jobs.values():
            intermediate_jobs.update(job["needs"])

        # Determine which jobs are final
        for job in self._jobs.values():
            if job["outfinal"] is None:
                job["outfinal"] = job["ID"] not in intermediate_jobs

        # Check for unique output files from final jobs
        out_final_files = set()
        for job in [job for job in self._jobs.values() if job["outfinal"]]:
            for out_file in job["outfiles"]:
                if out_file.name in out_final_files:
                    msg = (
                        f"final output file '{out_file.name}' is not unique to workflow"
                    )
                    raise WorkflowException(msg)
                out_final_files.add(out_file.name)

        # Check existing workflows for duplicates
        existing_workflow_IDs = []
        if basebatchdir.is_dir():
            existing_workflow_IDs = [
                d.name
                for d in basebatchdir.iterdir()
                if d.is_dir() and d.name != self.ID
            ]
        for existing_workflow_ID in existing_workflow_IDs:
            existing_workflow_batchdir = basebatchdir / existing_workflow_ID

            # Check whether workflow has been submitted
            existing_workflow_submitted = (
                existing_workflow_batchdir / "workflow_submitted"
            ).is_file()

            # Check for duplicate jobs
            existing_workflow_jobs: Set[Path] = set()
            existing_workflow_jobs_file = (
                existing_workflow_batchdir / "workflow_all_jobs"
            )
            if existing_workflow_jobs_file.is_file():
                existing_workflow_jobs.update(
                    Path(line)
                    for line in existing_workflow_jobs_file.read_text().split()
                )
            this_workflow_jobs = set(job["dir"] for job in self._jobs.values())
            num_duplicate_jobs = len(existing_workflow_jobs & this_workflow_jobs)
            all_duplicate_jobs = existing_workflow_jobs.issubset(this_workflow_jobs)

            # Check for duplicate outputs
            existing_workflow_outputs: Set[str] = set()
            existing_workflow_outputs_file = (
                existing_workflow_batchdir / "workflow_outputs"
            )
            if existing_workflow_outputs_file.is_file():
                existing_workflow_outputs.update(
                    existing_workflow_outputs_file.read_text().split()
                )
            this_workflow_outputs = set()
            for job in self._jobs.values():
                this_workflow_outputs.update([outp.name for outp in job["outfiles"]])
            num_duplicate_outputs = len(
                existing_workflow_outputs & this_workflow_outputs
            )
            all_duplicate_outputs = existing_workflow_outputs.issubset(
                this_workflow_outputs
            )

            if (
                not existing_workflow_submitted
                and all_duplicate_jobs
                and all_duplicate_outputs
            ):

                # Remove duplicate workflow
                shutil.rmtree(existing_workflow_batchdir)
                _logger.info("removed duplicate workflow %s", existing_workflow_ID)

            elif num_duplicate_jobs > 0 or num_duplicate_outputs > 0:

                # Raise error on partial job/output duplication with existing workflow
                msg = f"this workflow duplicates {num_duplicate_jobs} jobs and {num_duplicate_outputs} outputs with existing workflow {existing_workflow_ID}"
                raise WorkflowException(msg)

        for job in ProgressLogger(
            self._jobs.values(),
            logger=_logger,
            msg="wrote %s job directories in workflow",
        ):

            # Make job directory
            job["dir"].mkdir(parents=True, exist_ok=True)

            # Remove job batch ID file
            jobbatchID_file = job["dir"] / "job_batchID"
            jobbatchID_file.unlink(missing_ok=True)

            # Remove job state file, if job was not successful or skipped
            jobstate_file = job["dir"] / "job_state"
            if jobstate_file.is_file():
                jobstate = jobstate_file.read_text().strip()
                if not jobstate.startswith("SUCCESS"):
                    jobstate_file.unlink(missing_ok=True)

            # Write JSON representation of arguments
            job_args_file = job["dir"] / "job_args.json"
            job["infiles"].append(job_args_file)
            with FileLineWriter(job_args_file) as f:
                f += job["args_json"]

            # Write job script
            job_script_file = job["dir"] / "job_script.sh"
            job["infiles"].append(job_script_file)
            with FileLineWriter(job_script_file) as f:
                f += "set -e"

                # Write job environment
                for var in self._env:
                    val = self._env[var].format(**job["resources"])
                    f += "export {}={}".format(var, shlex.quote(val))

                # Ensure job directory is searched first for commands
                f += 'export PATH="${PWD}:${PATH}"'
                f += "hash -r"

                # Write job commands
                for cmd in job["cmds"]:
                    f += cmd

        # Make clean batch system script directory
        if batchdir.exists():
            shutil.rmtree(batchdir)
        batchdir.mkdir(parents=True, exist_ok=False)

        # Write list of all job directories
        alljobsfile = batchdir / "workflow_all_jobs"
        with FileLineWriter(alljobsfile) as f:
            for job in self._jobs.values():
                f += job["dir"]

        # Write list of all output files
        joboutputsfile = batchdir / "workflow_outputs"
        with FileLineWriter(joboutputsfile) as f:
            for job in self._jobs.values():
                for outp in job["outfiles"]:
                    f += outp.name

        # Name of file containing directories of any failed jobs
        # - Remove any existing file
        failedjobfile = batchdir / "workflow_failed_jobs"
        failedjobfile.unlink(missing_ok=True)

        # Name of file which workflow will write final state to
        # - Remove any existing file
        workstatefile = batchdir / "workflow_state"
        workstatefile.unlink(missing_ok=True)

        # Make directory for final output files
        outfinaldir = self.workdir / "outputs"
        outfinaldir.mkdir(parents=True, exist_ok=True)

        # Write batch system scripts
        self._workflow_writer.write_workflow(
            ID=self.ID,
            batchdir=batchdir,
            workflowname=self.name,
            account=self.account,
            jobs=self._jobs,
            outfinaldir=outfinaldir,
            failedjobfile=failedjobfile,
            workstatefile=workstatefile,
            submittedfile=submittedfile,
        )

        # List of all workflow IDs that have been written,
        # generated from batch system script directories
        all_workflow_IDs = sorted(
            [d.name for d in basebatchdir.iterdir() if d.is_dir()]
        )
        all_workflow_IDs_str = " ".join(all_workflow_IDs)

        # Get top-level control script commands
        submit, queue, cancel = self._workflow_writer.get_ctrl_commands(
            workflowname=self.name, basebatchdir=basebatchdir
        )

        # Write top-level control script: submit all jobs
        with FileLineWriter(self.workdir / "submit", executable=True) as f:
            f += f"""
            #!/bin/bash
            set -e
            if test "X$1" != X; then
                echo "usage: $0" >&2
                exit 1
            fi
            workflow_IDs=
            for ID in {all_workflow_IDs_str}; do
                state_file="{basebatchdir}/${{ID}}/workflow_state"
                failed_file="{basebatchdir}${{ID}}/workflow_failed_jobs"
                sub_file="{basebatchdir}/${{ID}}/workflow_submitted"
                if test -f "${{state_file}}"; then
                    workstate=`cat ${{state_file}}`
                    if test "X${{workstate}}" = XSUCCESS; then
                        echo "info: workflow ${{ID}} has already been successfully run"
                        continue
                    fi
                elif test -f "${{sub_file}}"; then
                    echo "info: workflow ${{ID}} has already been submitted"
                    continue
                fi
                workflow_IDs="${{workflow_IDs}} ${{ID}}"
                rm -f "${{state_file}}" "${{failed_file}}" "${{sub_file}}"
            done
            if test "X${{workflow_IDs}}" = X; then
                echo "info: no workflows to submit"
                exit 1
            fi
            """
            f += submit
            f += f"""
            submit ${{workflow_IDs}}
            msg=
            for ID in ${{workflow_IDs}}; do
                state_file="{basebatchdir}/${{ID}}/workflow_state"
                if test -f "${{state_file}}"; then
                    workstate=`cat ${{state_file}}`
                    if test "X${{workstate}}" = XSUCCESS; then
                        echo "info: workflow ${{ID}} has already been successfully run"
                        continue
                    fi
                fi
                sub_file="{basebatchdir}/${{ID}}/workflow_submitted"
                echo SUBMITTED > ${{sub_file}}
                if test "X${{msg}}" != X; then
                    msg="s${{msg}}"
                fi
                msg="${{msg}} ${{ID}}"
            done
            if test "X${{msg}}" != X; then
                echo "info: submitted all jobs in workflow${{msg}}"
            fi
            """

        # Write top-level control script: display all queued jobs
        with FileLineWriter(self.workdir / "queue", executable=True) as f:
            f += """
            #!/bin/bash
            set -e
            if test "X$1" != X; then
                echo "usage: $0" >&2
                exit 1
            fi
            """
            f += queue
            f += f"""
            queue {all_workflow_IDs_str}
            """

        # Write top-level control script: cancel all jobs
        with FileLineWriter(self.workdir / "cancel", executable=True) as f:
            f += """
            #!/bin/bash
            set -e
            if test "X$1" != X; then
                echo "usage: $0" >&2
                exit 1
            fi
            """
            f += cancel
            f += f"""
            cancel {all_workflow_IDs_str}
            """

        # Write top-level control script: wait for all workflows to complete
        with FileLineWriter(self.workdir / "wait", executable=True) as f:
            all_workstatefiles_exist = " && ".join(
                [
                    f"test -f '{f}'"
                    for f in [
                        basebatchdir / ID / "workflow_state" for ID in all_workflow_IDs
                    ]
                ]
            )
            f += f"""
            #!/bin/bash
            set -e
            sleepfor=5.0
            case "$1" in
                -h|--help)
                    echo "usage: $0 [-n sleepfor] [command args...]" >&2
                    exit 1
                    ;;
                -n)
                    shift
                    sleepfor="$1"
                    shift
            esac
            until {all_workstatefiles_exist}; do
                "$@"
                sleep ${{sleepfor}}
            done
            allworkstate=SUCCESS
            for ID in {all_workflow_IDs_str}; do
                state_file="{basebatchdir}/${{ID}}/workflow_state"
                workstate=`cat ${{state_file}}`
                echo "workflow state: ${{workstate}} (${{ID}})"
                if test "X${{workstate}}" != XSUCCESS; then
                    allworkstate=FAILED
                fi
            done
            echo "workflow state: ${{allworkstate}}"
            """

        # Log completion
        _logger.info("workflow has been written")
