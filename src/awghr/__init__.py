# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""A(nother) Workflow Generator for High-throughput computeRing.

Configuration:
    Default workflow configurations are retrieved from ``~/.awghr.cfg``.

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

import argparse
import json
import math
import os
import re
import shlex
import shutil
import sys
from datetime import datetime
from hashlib import blake2b
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pint import Quantity

from ._config import batch_system_names, cluster_names, read_config
from ._util import (
    LOGGER,
    SCRIPT_HEADER,
    FileLineWriter,
    JobFileList,
    ProgressLogger,
    WorkflowException,
    job_dir_from_ID,
    write_atomic,
)

__author__ = "Karl Wette"
__version__ = "0.1"


class Workflow:
    """Generate a workflow.

    Args:
        name (str):
            Name of workflow.

            If ``subdir`` is given, workflow will be written to the path
            ``basedir / subdir / name``, otherwise is will be written to ``basedir / name``.

        tags (Optional[List[str]]):
            List of tags, used to retrieve default arguments from ``~/.awghr.cfg``.

        basedir (Optional[Path]):
            Base workflow directory.
            If not given, use the default value given in ``~/.awghr.cfg``,
            or else the current directory.

        subdir (Optional[Path]):
            Subdirectory under the base workflow directory.

        account (Optional[Union[str, Dict[str, str]]]):
            Account the workflow should be charged against.

            If a dictionary, lists different accounts for different clusters
            and/or batch systems. The cluster name is defined by the user in
            ``~/.awghr.cfg``; the batch system is determined
            automatically on the computer cluster where the workflow is
            being generated. If selection based on both cluster name and
            batch system is required, they are given (in any order)
            separated by an underscore. Example:

            * ``bigcomputer`` selects a cluster named ``bigcomputer``;
            * ``htcondor`` selects the HTCondor batch system;
            * ``generic_slurm`` selects a cluster named ``generic`` running
              the Slurm batch system.

            If not given, use the default value given in ``~/.awghr.cfg``;
            if no default value is defined, an error is raised.

        skip_exit_code (Optional[int]):
            Exit code used to indicate that a job should be skipped.
            Default is 77.

    Attributes:
        name (str):
            Name of workflow. Must start with a letter, and may contain only
            letters, numbers, dashes, and underscores.

        workdir (Path):
            Working directory where workflow will be written.

        cluster (str):
            Name of the computer cluster where the workflow is being generated.
            Cluster names are defined by the user by matching against batch system,
            host name, etc. in a configuration section in ``~/.awghr.cfg``.

        batch_system (str):
            Batch system of the computer cluster where the workflow is being generated.

        account (str):
            Account the workflow should be charged against.
            If not given, use the default value given in ``~/.awghr.cfg``.

        ID (str):
            A unique string representing the workflow.
            Only available after :py:meth:`Workflow.write` has been called.

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
        subdir: Optional[Path] = None,
        account: Optional[Union[str, Dict[str, str]]] = None,
        skip_exit_code: Optional[int] = 77,
    ):

        # Read configuration
        LOGGER.info("reading configuration")
        cfg = read_config(tags=tags)

        # Check name
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name, re.IGNORECASE):
            msg = (
                f"workflow name '{name}' must start with a letter,"
                + " and may contain only letters, numbers, dashes, and underscores"
            )
            raise WorkflowException(msg)

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
        self.workdir = basedir / (Path(".") if subdir is None else subdir) / self.name

        # Set name of cluster
        self.cluster = cfg["cluster"]

        # Set batch system
        self.batch_system = cfg["batch_system"]

        # Set account
        self.account = self._select_on_cluster_batch_system(
            "account", account, always=False, required=False, multi=False
        )
        if self.account is None:
            self.account = cfg["account"]
        if self.account is None:
            msg = "no account has been defined"
            raise WorkflowException(msg)

        # Set internal variables
        self._tags = tags
        self._skip_exit_code = skip_exit_code
        self._workflow_writer = cfg["workflow_writer"](
            batchsyscfg=cfg["batch_system_cfg"]
        )
        self._workflow_hash = blake2b(digest_size=8, usedforsecurity=False)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._log_add_jobs = ProgressLogger(msg="added %s jobs")

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
        LOGGER.info("workflow name: %s", self.name)
        LOGGER.info("workflow account: %s", self.account)
        LOGGER.info("workflow directory: %s", self.workdir)
        LOGGER.info("batch system: %s", self.batch_system)

    def _select_on_cluster_batch_system(self, label, arg, *, always, required, multi):
        """Select argument based on cluster name and/or batch system."""

        # If no cluster name and/or batch system options, just return arg
        if not always and not isinstance(arg, dict):
            return arg

        # Iterate over cluster name/batch system selectors
        selected_args = []
        for cluster_batch_system_str, cluster_batch_system_arg in arg.items():

            # Check selectors are valid cluster name or batch systems
            cluster_batch_system = cluster_batch_system_str.split("_")
            for s in cluster_batch_system:
                if s not in cluster_names and s not in batch_system_names():
                    msg = f"'{s}' selector for '{label}' does not match either a cluster name or a batch system name"
                    raise WorkflowException(msg)

            # Selection found
            if (
                self.cluster in cluster_batch_system
                or self.batch_system in cluster_batch_system
            ):
                selected_args.append(cluster_batch_system_arg)

        # Return selections
        if len(selected_args) > 0:
            if multi:
                return selected_args
            else:
                if len(selected_args) > 1:
                    msg = f"multiple '{label}' matches for cluster name '{self.cluster}' and/or batch system '{self.batch_system}'"
                    raise WorkflowException(msg)
                else:
                    return selected_args[0]

        # No selection found
        if required:
            msg = f"no '{label}' match for cluster name '{self.cluster}' and/or batch system '{self.batch_system}'"
            raise WorkflowException(msg)
        else:
            return [] if multi else None

    def add_job(
        self,
        *,
        args: Dict[str, Any],
        cmds: List[Union[str, List[Any]]],
        time: Union[Tuple[float, str], Dict[str, Tuple[float, str]]],
        mem: Union[Tuple[float, str], Dict[str, Tuple[float, str]]],
        disk: Union[Tuple[float, str], Dict[str, Tuple[float, str]]],
        ncpus: Union[int, Dict[str, int]] = 1,
        ngpus: Union[int, Dict[str, int]] = 0,
        env: Optional[Dict[str, Any]] = None,
        needs: Optional[List[str]] = None,
        inputs: Optional[List[Union[Path, Tuple[str, ...]]]] = None,
        outputs: List[str],
        batchopt: Optional[Dict[str, Dict[str, Any]]] = None,
        final: Optional[bool] = None,
    ) -> str:
        """Add a new job to the workflow.

        Args:
            args (Dict[str, Any]):
                Arguments and their values which uniquely identify the job.
                Argument names must be valid Python identifiers, and cannot
                begin with an underscore.

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
                * ``_id``: the unique string representing the job.
                * ``_in``: input file(s) supplied in ``inputs``, separated by spaces.
                * ``_in[re:sep]``: the input file(s) supplied in ``inputs`` that
                  match the regular expression ``re``, separated by ``sep``.
                  Both arguments are optional.
                  Example: ``_in[\\.dat$:,]`` returns file(s) with ``.dat`` extensions,
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

            time (Union[Tuple[float, str], Dict[str, Tuple[float, str]]]):
                Requested wall time of the job: ``(magnitude, "unit")``

                If a dictionary, lists different resource requirements for
                different clusters and/or batch systems; see the ``account``
                option to :py:meth:`Workflow`.

            mem (Union[Tuple[float, str], Dict[str, Tuple[float, str]]]):
                Requested memory of the job: ``(magnitude, "unit")``

                If a dictionary, lists different resource requirements for
                different clusters and/or batch systems; see the ``account``
                option to :py:meth:`Workflow`.

            disk (Union[Tuple[float, str], Dict[str, Tuple[float, str]]]):
                Requested temporary disk space of the job: ``(magnitude, "unit")``

                If a dictionary, lists different resource requirements for
                different clusters and/or batch systems; see the ``account``
                option to :py:meth:`Workflow`.

            ncpus (Union[int, Dict[str, int]]):
                Requested number of CPUs of the job.
                Default is 1.

                If a dictionary, lists different resource requirements for
                different clusters and/or batch systems; see the ``account``
                option to :py:meth:`Workflow`.

            ngpus (Union[int, Dict[str, int]]):
                Requested number of GPUs of the job.
                Default is 0.

                If a dictionary, lists different resource requirements for
                different clusters and/or batch systems; see the ``account``
                option to :py:meth:`Workflow`.

            env (Optional[Dict[str, Any]]):
                List of environment variables to save/set in job script.
                Overrides any values given in ``~/.awghr.cfg``.

            needs (Optional[List[str]]):
                A list of IDs of prerequisite jobs in the same workflow.

            inputs (Optional[List[Union[Path, Tuple[str, ...]]]]):
                A list of input files, either:

                * a path to a file not generated by the workflow; or
                * a tuple (job ID, file name, ...) representing file(s) generated
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

                * The keys of the outer dictionary lists different accounts for
                  different clusters and/or batch systems; see the ``account``
                  option to :py:meth:`Workflow`.

                * The values of the outer dictionary are themselves dictionaries
                  of options for the given cluster/batch system. Only options
                  given for this cluster/batch system combination are used;
                  other options are silently ignored.

            final (Optional[bool]):
                If true, output files from this job will be copied to the
                ``outputs/`` workflow directory. If not given, all jobs
                which are not dependencies of other jobs will have their output
                files copied to this directory.

        Returns:
            str: Job ID, a unique string representing the job.

        """

        # Check argument names
        for arg in args.keys():
            if not arg.isidentifier() or arg.startswith("_"):
                msg = (
                    f"argument name '{arg}' must be a valid Python identifier,"
                    + " and cannot begin with an underscore"
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
        res_val: Any
        for res_val, res_name, res_unit in (
            (time, "time", "min"),
            (mem, "mem", "MB"),
            (disk, "disk", "MB"),
        ):
            _res_val = self._select_on_cluster_batch_system(
                res_name, res_val, always=False, required=True, multi=False
            )
            job[f"{res_name}_{res_unit}"] = int(
                math.ceil(Quantity(_res_val[0], _res_val[1]).to(res_unit).magnitude)
            )
        for res_val, res_name in ((ncpus, "ncpus"), (ngpus, "ngpus")):
            job[res_name] = self._select_on_cluster_batch_system(
                res_name, res_val, always=False, required=True, multi=False
            )
        job["resources"] = {
            f"_{k}": job[k] for k in {"time_min", "mem_MB", "disk_MB", "ncpus", "ngpus"}
        }

        # Store job environment
        job["env"] = self._env.copy()
        if env is not None:
            job["env"].update(env)

        # Create job directory
        jobdir = job_dir_from_ID(self.workdir / "jobs", job_ID)
        job["dir"] = jobdir

        # Parse input file list
        job["infiles"] = []
        need_job_IDs = set()
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
                    inp_job_ID = inp[0]
                    inp_filenames = inp[1:]
                    if inp_job_ID not in self._jobs:
                        msg = f"job ID '{inp_job_ID}' for input file(s) does not exist"
                        raise WorkflowException(msg)
                    if not inp_filenames:
                        msg = f"no input file(s) for job ID '{inp_job_ID}'"
                        raise WorkflowException(msg)
                    need_job_IDs.add(inp_job_ID)
                    for inp_filename in inp_filenames:
                        inp_file = self._jobs[inp_job_ID]["dir"] / inp_filename.format(
                            **self._jobs[inp_job_ID]["args"], _id=inp_job_ID
                        )
                        job["infiles"].append(inp_file)

        # Parse job dependencies and dependency order
        job["needs"] = []
        job["order"] = 0
        needs_hash = blake2b(digest_size=8, usedforsecurity=False)
        if needs is not None:
            need_job_IDs.update(needs)
        for need_job_ID in sorted(need_job_IDs):
            if need_job_ID not in self._jobs:
                msg = f"job ID '{need_job_ID}' does not exist"
                raise WorkflowException(msg)
            job["needs"].append(need_job_ID)
            job["order"] = max(job["order"], self._jobs[need_job_ID]["order"] + 1)
            args_json_bytes = self._jobs[need_job_ID]["args_json"].encode("utf-8")
            needs_hash.update(args_json_bytes)
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
        format_cmd["_id"] = job_ID
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
                for cmd_arg in cmd:
                    if isinstance(cmd_arg, dict):

                        # Format dict argument as ``key=value``
                        cmd_args.extend(
                            [
                                f"{k}={v}".format(**format_cmd)
                                for k, v in cmd_arg.items()
                            ]
                        )

                    elif isinstance(cmd_arg, Path):

                        # Stage path in input files, format as local path
                        cmd_arg = cmd_arg.resolve()
                        if cmd_arg not in job["infiles"]:
                            job["infiles"].append(cmd_arg)
                        cmd_args.append("./" + cmd_arg.name)

                    else:

                        # Format all other arguments as strings
                        cmd_args.append(str(cmd_arg).format(**format_cmd))

                # Quote arguments
                cmd = " ".join(shlex.quote(arg) for arg in cmd_args)

            job["cmds"].append(cmd)

        # Add batch system-specific options
        job["batchopt"] = {}
        if batchopt:
            for _batchopt in self._select_on_cluster_batch_system(
                "batchopt",
                batchopt,
                always=True,
                required=False,
                multi=True,
            ):
                for opt, val in _batchopt.items():
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
        """Retrieve the value of a job argument.

        Arguments:
            key (Tuple[str, str]):
                First key is the job ID of the job, as returned by
                :py:meth:`Workflow.add_job`. Second key is the name of the job
                argument, as passed in the ``args`` argument to
                :py:meth:`Workflow.add_job`.

        Returns:
            Any: Value of the job argument.

        """
        return self._jobs[key[0]]["args"][key[1]]

    def write(self) -> None:
        """Write workflow files and directories."""

        # Log progress
        self._log_add_jobs.finish()

        # Generate workflow ID as hash of all job JSON representations
        self.ID = self._workflow_hash.hexdigest()
        assert self.ID != "0" * 16

        # Directory containing workflows
        baseworkflowdir = self.workdir / "workflows"
        workflowdir = baseworkflowdir / self.ID

        # Name of file indicating workflow has been submitted
        submittedfile = workflowdir / "submitted"

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
        if baseworkflowdir.is_dir():
            existing_workflow_IDs = [
                d.name
                for d in baseworkflowdir.iterdir()
                if d.is_dir() and d.name != self.ID
            ]
        for existing_workflow_ID in existing_workflow_IDs:
            existing_workflow_workflowdir = baseworkflowdir / existing_workflow_ID

            # Check whether workflow has been submitted
            existing_workflow_submitted = (
                existing_workflow_workflowdir / "submitted"
            ).is_file()

            # Check for duplicate jobs that have not been run successfully
            existing_workflow_jobs: Set[Path] = set()
            existing_workflow_jobs_file = existing_workflow_workflowdir / "all_jobs"
            if existing_workflow_jobs_file.is_file():
                existing_workflow_jobs.update(
                    Path(line)
                    for line in existing_workflow_jobs_file.read_text().split()
                )
            this_workflow_jobs = set(job["dir"] for job in self._jobs.values())
            duplicate_jobs = []
            duplicate_jobs_state = {}
            for job_dir in existing_workflow_jobs & this_workflow_jobs:
                job_state_file = job_dir / "job_state"
                if job_state_file.is_file():
                    job_state = job_state_file.read_text().strip()
                else:
                    job_state = "PENDING"
                if job_state != "SUCCESS":
                    duplicate_jobs.append(job_dir)
                duplicate_jobs_state[job_dir] = job_state
            num_duplicate_jobs = len(duplicate_jobs)

            # Check if all existing jobs are a subset of this workflow
            all_duplicate_jobs = existing_workflow_jobs.issubset(this_workflow_jobs)

            # Check for duplicate final outputs
            existing_workflow_outputs: Set[str] = set()
            existing_workflow_outputs_file = existing_workflow_workflowdir / "outputs"
            if existing_workflow_outputs_file.is_file():
                existing_workflow_outputs.update(
                    existing_workflow_outputs_file.read_text().split()
                )
            this_workflow_outputs = set()
            for job in self._jobs.values():
                this_workflow_outputs.update(
                    [outp.name for outp in job["outfiles"] if job["outfinal"]]
                )
            duplicate_outputs = list(existing_workflow_outputs & this_workflow_outputs)
            num_duplicate_outputs = len(duplicate_outputs)
            all_duplicate_outputs = existing_workflow_outputs.issubset(
                this_workflow_outputs
            )

            if (
                not existing_workflow_submitted
                and all_duplicate_jobs
                and all_duplicate_outputs
            ):

                # Remove duplicate workflow
                shutil.rmtree(existing_workflow_workflowdir)
                LOGGER.info("removed duplicate workflow %s", existing_workflow_ID)

            elif num_duplicate_jobs > 0 or num_duplicate_outputs > 0:

                # Raise error on partial job/output duplication with existing workflow
                msg = ""
                if num_duplicate_jobs > 0:
                    msg += f"this workflow duplicates {num_duplicate_jobs} jobs with existing workflow {existing_workflow_ID}:\n"
                    for job_dir in duplicate_jobs:
                        msg += f"    {job_dir} (job state: {duplicate_jobs_state[job_dir]})\n"
                if num_duplicate_outputs > 0:
                    msg += f"this workflow duplicates {num_duplicate_outputs} outputs with existing workflow {existing_workflow_ID}:\n"
                    for outp_name in duplicate_outputs:
                        msg += f"    {outp_name}\n"
                raise WorkflowException(msg)

        for job in ProgressLogger(
            self._jobs.values(),
            msg="wrote %s job directories",
        ):

            # Make job directory
            job["dir"].mkdir(parents=True, exist_ok=True)

            # Remove job batch ID file
            jobbatchID_file = job["dir"] / "job_batchID"
            jobbatchID_file.unlink(missing_ok=True)

            # Remove job files, if job was not successful or skipped
            job_state_file = job["dir"] / "job_state"
            if job_state_file.is_file():
                job_state = job_state_file.read_text().strip()
                if not job_state.startswith("SUCCESS"):
                    job_state_file.unlink(missing_ok=True)
                    for job_file in (
                        "job_batchID",
                        "job_stdout",
                        "job_stderr",
                        "job_usage",
                    ):
                        (job["dir"] / job_file).unlink(missing_ok=True)

            # Write JSON representation of arguments
            job_args_file = job["dir"] / "job_args.json"
            job["infiles"].append(job_args_file)
            with FileLineWriter(job_args_file) as f:
                f += job["args_json"]

            # Write job script
            job_script_file = job["dir"] / "job_script.sh"
            job["infiles"].append(job_script_file)
            job["joboutfiles"] = [job["dir"] / "job_usage"]
            with FileLineWriter(job_script_file) as f:
                f += "set -e"

                # Write job environment
                for var, val in job["env"].items():
                    val = str(val).format(**job["resources"])
                    f += "export {}={}".format(var, shlex.quote(val))

                # Ensure job directory is searched first for commands
                f += 'export PATH="${PWD}:${PATH}"'
                f += "hash -r"

                # Set up /usr/bin/time wrapper to record usage statistics
                # - First try the GNU variant:
                #     /usr/bin/time -q -v -o ./job_usage ...
                #   then try the BSD variant
                #     /usr/bin/time -p -l -o ./job_usage ...
                #   then give up
                f += """
                job_usage_wrapper='/usr/bin/time -q -v -o ./job_usage'
                if ! eval ${job_usage_wrapper} true >/dev/null 2>&1; then
                    job_usage_wrapper='/usr/bin/time -p -l -o ./job_usage'
                    if ! eval ${job_usage_wrapper} true >/dev/null 2>&1; then
                        job_usage_wrapper=''
                        echo 'WARNING: could not find a compatible /usr/bin/time' > ./job_usage
                    fi
                fi
                eval ${job_usage_wrapper} bash <<_AWGHR_EOF_
                """

                # Write job commands
                for cmd in job["cmds"]:
                    f += cmd

                # End /usr/bin/time wrapper
                f += "_AWGHR_EOF_"

        # Make workflow directory
        workflowdir.mkdir(parents=True, exist_ok=True)

        # Write workflow description
        workflow_job_keys = (
            "args",
            "batchopt",
            "dir",
            "disk_MB",
            "infiles",
            "joboutfiles",
            "mem_MB",
            "ncpus",
            "needs",
            "needs_ID",
            "ngpus",
            "order",
            "outfiles",
            "outfinal",
            "time_min",
        )
        workflow = {
            "ID": self.ID,
            "name": self.name,
            "tags": self._tags,
            "account": self.account,
            "jobs": {
                ID: {k: self._jobs[ID][k] for k in workflow_job_keys}
                for ID in self._jobs
            },
            "skip_exit_code": self._skip_exit_code,
        }
        workflow_file = workflowdir / "workflow.json"
        json.dump(workflow, workflow_file.open("w"), default=str, sort_keys=True)

        # Write workflow from description
        _write_workflow(workflow_file, self._workflow_writer)

        # List of all workflow IDs that have been written,
        # generated from workflow directories
        all_workflow_IDs = sorted(
            [d.name for d in baseworkflowdir.iterdir() if d.is_dir()]
        )
        all_workflow_IDs_str = " ".join(all_workflow_IDs)

        # Get top-level control script commands
        submit, queue, cancel = self._workflow_writer.get_ctrl_commands(
            workflowname=self.name, baseworkflowdir=baseworkflowdir
        )

        # Write top-level control script: submit all jobs
        with FileLineWriter(self.workdir / "submit", executable=True) as f:
            f += """
            #!/bin/bash
            """
            f += SCRIPT_HEADER
            f += f"""
            if test "X$1" != X; then
                echo "usage: ${{0##*/}}" >&2
                exit 1
            fi
            workflow_IDs=
            for ID in {all_workflow_IDs_str}; do
                state_file="{baseworkflowdir}/${{ID}}/state"
                failed_file="{baseworkflowdir}${{ID}}/failed_jobs"
                sub_file="{baseworkflowdir}/${{ID}}/submitted"
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
                workflow_file="{baseworkflowdir}/${{ID}}/workflow.json"
                timestamp_file="{baseworkflowdir}/${{ID}}/batch_scripts/.timestamp"
                if test "${{workflow_file}}" -nt "${{timestamp_file}}"; then
                    {sys.executable} -m awghr.__init__ write-workflow "${{workflow_file}}"
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
                state_file="{baseworkflowdir}/${{ID}}/state"
                if test -f "${{state_file}}"; then
                    workstate=`cat ${{state_file}}`
                    if test "X${{workstate}}" = XSUCCESS; then
                        echo "info: workflow ${{ID}} has already been successfully run"
                        continue
                    fi
                fi
                sub_file="{baseworkflowdir}/${{ID}}/submitted"
                write_atomic SUBMITTED ${{sub_file}}
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
            """
            f += SCRIPT_HEADER
            f += """
            if test "X$1" != X; then
                echo "usage: ${0##*/}" >&2
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
            """
            f += SCRIPT_HEADER
            f += """
            if test "X$1" != X; then
                echo "usage: ${0##*/}" >&2
                exit 1
            fi
            """
            f += cancel
            f += f"""
            cancel {all_workflow_IDs_str}
            """

        # Write top-level control script: wait for all workflows to complete
        with FileLineWriter(self.workdir / "await", executable=True) as f:
            all_workstatefiles_exist = " && ".join(
                [
                    f"test -f '{f}'"
                    for f in [baseworkflowdir / ID / "state" for ID in all_workflow_IDs]
                ]
            )
            f += """
            #!/bin/bash
            """
            f += SCRIPT_HEADER
            f += f"""
            sleepfor=5.0
            case "$1" in
                -h|--help)
                    echo "usage: ${{0##*/}} [-n sleepfor] [command args...]" >&2
                    exit 1
                    ;;
                -n)
                    shift
                    sleepfor="$1"
                    shift
            esac
            export PAGER=cat
            until {all_workstatefiles_exist}; do
                echo "${{0##*/}}: `date`"
                "$@" </dev/null 2>&1
                sleep ${{sleepfor}}
            done
            allworkstate=SUCCESS
            for ID in {all_workflow_IDs_str}; do
                state_file="{baseworkflowdir}/${{ID}}/state"
                workstate=`cat ${{state_file}}`
                echo "workflow state: ${{workstate}} (${{ID}})"
                if test "X${{workstate}}" != XSUCCESS; then
                    allworkstate=FAILED
                fi
            done
            echo "workflow state: ${{allworkstate}}"
            """

        # Write top-level control script: manage jobs
        with FileLineWriter(self.workdir / "manage", executable=True) as f:
            f += """
            #!/bin/bash
            """
            f += SCRIPT_HEADER
            f += f"""
            cd '{self.workdir}'
            exec {sys.executable} -m awghr._manage "$@"
            """

        # Log completion
        LOGGER.info("all workflow files and directories have been written")


def _write_workflow(workflow_file, workflow_writer=None):
    """Write workflow."""

    # Load workflow from description
    # - convert paths into Path()s
    workflow = json.load(workflow_file.open("r"))
    for job_ID, job in workflow["jobs"].items():
        job["ID"] = job_ID
        job["dir"] = Path(job["dir"])
        for k in ("infiles", "joboutfiles", "outfiles"):
            job[k] = [Path(v) for v in job[k]]
    LOGGER.info("read workflow from %s", workflow_file)

    if workflow_writer is None:

        # Read configuration
        LOGGER.info("reading configuration")
        cfg = read_config(tags=workflow["tags"])

        # Get workflow writer
        workflow_writer = cfg["workflow_writer"](batchsyscfg=cfg["batch_system_cfg"])

    # Directory containing workflows
    workflowdir = workflow_file.parent

    # Directory containing batch system scripts
    batchdir = workflow_file.parent / "batch_scripts"

    # Make clean batch system script directory
    if batchdir.exists():
        shutil.rmtree(batchdir)
    batchdir.mkdir(parents=True, exist_ok=False)

    # Write list of all job directories
    alljobsfile = workflowdir / "all_jobs"
    with FileLineWriter(alljobsfile) as f:
        for job in workflow["jobs"].values():
            f += job["dir"]

    # Write list of all final output files
    joboutputsfile = workflowdir / "outputs"
    with FileLineWriter(joboutputsfile) as f:
        for job in workflow["jobs"].values():
            if job["outfinal"]:
                for outp in job["outfiles"]:
                    f += outp.name

    # Name of file containing directories of any failed jobs
    # - Remove any existing file
    failedjobfile = workflowdir / "failed_jobs"
    failedjobfile.unlink(missing_ok=True)

    # Name of file which workflow will write final state to
    # - Remove any existing file
    workstatefile = workflowdir / "state"
    workstatefile.unlink(missing_ok=True)

    # Make directory for final output files
    outfinaldir = workflowdir.parent.parent / "outputs"

    # Name of file indicating workflow has been submitted
    submittedfile = workflowdir / "submitted"

    # Write workflow
    workflow_writer.write_workflow(
        ID=workflow["ID"],
        batchdir=batchdir,
        workflowname=workflow["name"],
        account=workflow["account"],
        jobs=workflow["jobs"],
        outfinaldir=outfinaldir,
        failedjobfile=failedjobfile,
        workstatefile=workstatefile,
        submittedfile=submittedfile,
        skip_exit_code=workflow["skip_exit_code"],
    )

    # Write timestamp
    write_atomic(str(datetime.now()), batchdir / ".timestamp")

    # Log completion
    LOGGER.info("workflow has been written")


def _cli(*argv: str):
    """Command-line interface: manage workflows."""

    # Build command-line parser
    parser = argparse.ArgumentParser(prog="workflows", description="manage workflows")
    subparsers = parser.add_subparsers(required=True, help="sub-commands")
    parser_write = subparsers.add_parser("write-workflow", help="write workflow")
    parser_write.add_argument("workflow_file", type=Path, help="workflow file")
    parser_write.set_defaults(
        subcommand=lambda args: _write_workflow(args.workflow_file)
    )

    # Parse command line
    args = parser.parse_args(argv if len(argv) > 0 else sys.argv[1:])

    # Execute sub-command
    args.subcommand(args)

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
