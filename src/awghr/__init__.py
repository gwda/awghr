import json
import logging
import math
import os
import re
import stat
import sys
from datetime import datetime
from hashlib import blake2b
from pathlib import Path
from shlex import quote
from shutil import which
from typing import Any, Dict, List, Optional, Tuple, Union

from pint import Unit, UnitRegistry

from .config import read_config
from .util import FileLineWriter, ProgressLogger, job_dir_from_ID

__author__ = "Karl Wette"
__version__ = "0.1"

_ureg = UnitRegistry()

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
_logger = logging.getLogger(__name__)


class Workflow:
    """
    Generate a workflow.

    Default workflow configurations are retrieved from `~/.awghr.cfg`.
    This is a `ConfigParser`-style configuration file with the following syntax:
    ```
    [DEFAULT]           # This section match all batch systems/hosts/tags
    account = ...           # Default value of `account` argument passed to Workflow()
    basedir = ...           # Default value of `basedir` argument passed to Workflow()
    dated? = ...            # Default value of `dated` argument passed to Workflow()
    envlist =               # List of environment variables to save/set in job script:
        *PATH               # - Save any variables matching this regular expression.
        VAR=val             # - Set variable to the given value.
        ...                 #   May contain `{format}` strings which will be filled by:
                            #   * `_time_min`: requested wall time in minutes.
                            #   * `_mem_MiB`: requested memory in MiB.
                            #   * `_disk_MiB`: requested temporary disk space in MiB.
                            #   * `_threads`: requested number of threads.

    # Section headings are arbitrary
    [host matching]     # This section matches the host `login1.example.com`
    match host = login1.example.com
    match host = .example.com
    match host = login1
    match host = login
    ...

    [batch matching]    # This section matches a given batch system
    match batch = ...
    # Batch system matching sections may include system-specific keys

    [tag matching]      # This section matches a tag in the `tags` list passed to Workflow()
    match tag = ...
    ...
    ```
    """

    # Useful units of time
    MIN = _ureg.Quantity(1.0, "minute")
    HOUR = _ureg.Quantity(1.0, "hour")
    DAY = _ureg.Quantity(1.0, "day")

    # Useful units of size
    ZB = _ureg.Quantity(0.0, "byte")
    MiB = _ureg.Quantity(1.0, "mebibyte")
    GiB = _ureg.Quantity(1.0, "gibibyte")

    def __init__(
        self,
        name: str,
        *,
        tags: Optional[List[str]] = None,
        account: Optional[str] = None,
        basedir: Optional[Path] = None,
        dated: Optional[bool] = None,
    ):
        """
        Create a new workflow.

        Args:
            name (str):
                Name of workflow.
            tags (Optional[List[str]]):
                List of tags, used to retrieve default arguments from `~/.awghr.cfg`.
            account (Optional[str]):
                Accounting tag the workflow should be charged against.
                If not given, use the default value retrieved from `~/.awghr.cfg`.
            basedir (Optional[Path]):
                Base directory for all workflows; this workflow will be written to a subdirectory.
                If not given, use the default value retrieved from `~/.awghr.cfg`,
                or else the current directory.
            dated (Optional[bool]):
                If true, the workflow subdirectory in `basedir` will be named after the current
                date and time, plus `name`. If false, the workflow subdirectory will be just `name`.
                If not given, use the default value retrieved from `~/.awghr.cfg`, or else true.
        """

        # Read configuration
        _logger.info("reading configuration")
        cfg = read_config(tags=tags)

        # Get default values
        if account is None:
            account = cfg["account"]
        if basedir is None:
            basedir = cfg["basedir"]
        if dated is None:
            dated = cfg["dated?"]

        # Check input values
        try:
            basedir = basedir.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            msg = f"Base directory '{basedir}' is not a directory"
            raise ValueError(msg)

        # Initialise internal variables
        self._name = name
        self._account = account
        self._workflow_writer = cfg["workflow_writer"](cfg["batch_system_cfg"])
        self._workflow_hash = blake2b(digest_size=8, usedforsecurity=False)
        self._jobs: Dict[str, Dict[str, Any]] = dict()
        self._log_add_jobs = ProgressLogger(_logger, "added %s jobs to workflow")

        # Add workflow directory to base directory
        if dated:
            workdirname = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_") + self._name
        else:
            workdirname = self._name
        self._workdir = basedir / workdirname

        # Process environment variables
        self._env = dict()
        for env in cfg["envlist"]:
            if "=" not in env:
                for var in os.environ:
                    if re.fullmatch(env, var) is not None:
                        self._env[var] = os.environ[var]
        for env in cfg["envlist"]:
            if "=" in env:
                var, val = env.split("=", maxsplit=1)
                self._env[var] = val

        # Log details
        _logger.info("workflow name: %s", self._name)
        _logger.info("workflow account: %s", self._account)
        _logger.info("workflow directory: %s", self._workdir)

    def add_job(
        self,
        *,
        args: Dict[str, Any],
        cmds: List[List[Any]],
        time: Unit,
        mem: Unit,
        disk: Unit,
        threads: int = 1,
        inputs: Optional[List[Union[Path, Tuple[str, str]]]] = None,
        outputs: List[str],
        final: Optional[bool] = None,
    ) -> str:
        """
        Add a new job to the workflow.

        Args:
            args (Dict[str, Any]):
                Arguments and their values which uniquely identify the job.
                Argument names cannot begin with an underscore.
            cmds (List[List[Any]]):
                A list of commands and their arguments to execute.
                May contain `{format}` strings which will be filled by:
                * the dictionary supplied in `args`;
                * `_in[n]`: the `n`th input file supplied in `inputs`;
                * `_out[n]`: the `n`th output file supplied in `outputs`.
                * `_time_min`: requested wall time in minutes.
                * `_mem_MiB`: requested memory in MiB.
                * `_disk_MiB`: requested temporary disk space in MiB.
                * `_threads`: requested number of threads.
            time (Unit):
                Requested wall time of the job.
                Use the pre-defined constants:
                * `Workflow.MIN`: 1 minute.
                * `Workflow.HOUR`: 60 minutes.
                * `Workflow.DAY`: 1440 minutes.
            mem (Unit):
                Requested memory of the job.
                Use the pre-defined constants:
                * `Workflow.ZB`: zero bytes.
                * `Workflow.MiB`: 2^20 bytes.
                * `Workflow.GiB`: 2^30 bytes.
            disk (Unit):
                Requested temporary disk space of the job.
                Use the same pre-defined constants as for `mem`.
            threads (int):
                Requested number of threads of the job.
                Default is 1.
            inputs (Optional[List[Union[Path, Tuple[str, str]]]]):
                A list of input files, either:
                * a path to a file not generated by the workflow; or
                * a tuple (job ID, file name) representing a file generated
                  by a previous job in the workflow.
                  May contain `{format}` strings which will be filled by:
                  * the dictionary supplied in `args` to the previous job;
                  * `_id`: the unique string representing the previous job.
                Input files will be staged to the working directory of the job.
            outputs (List[str]):
                A list of output files generated by the job.
                May contain `{format}` strings which will be filled by:
                * the dictionary supplied in `args`;
                * `_id`: the unique string representing the job.
                Output files will be staged out of the working directory of the job.
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
            raise ValueError(msg)

        # Generate JSON representation of arguments
        args_json = json.dumps(args, sort_keys=True)

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
            raise ValueError(msg)
        job: Dict[str, Any] = dict()
        job["ID"] = job_ID

        # Store job arguments
        job["args"] = args
        job["args_json"] = args_json

        # Normalise and store resource requests
        job["time_min"] = int(math.ceil(time.to(self.MIN).magnitude))
        job["mem_MiB"] = int(math.ceil(mem.to(self.MiB).magnitude))
        job["disk_MiB"] = int(math.ceil(disk.to(self.MiB).magnitude))
        job["threads"] = threads
        job["resources"] = {
            f"_{k}": job[k] for k in {"time_min", "mem_MiB", "disk_MiB", "threads"}
        }

        # Create job directory
        jobdir = job_dir_from_ID(self._workdir / "jobs", job_ID)
        job["dir"] = jobdir

        # Parse input file list, job dependencies, and dependency order
        job["infiles"] = []
        job["needs"] = []
        job["order"] = 0
        needs_hash = blake2b(digest_size=8, usedforsecurity=False)
        if inputs is not None:
            if len(inputs) != len(set(inputs)):
                msg = "'inputs' is not a unique list"
                raise ValueError(msg)
            for inp in inputs:
                if isinstance(inp, Path):
                    if not inp.is_file():
                        msg = f"external input file '{inp}' does not exist"
                        raise ValueError(msg)
                    job["infiles"].append(inp)
                else:
                    inp_job_ID, inp_filename = inp
                    if inp_job_ID not in self._jobs:
                        msg = f"job for input file '{inp_filename}' does not exist"
                        raise ValueError(msg)
                    inp_file = self._jobs[inp_job_ID]["dir"] / inp_filename.format(
                        **self._jobs[inp_job_ID]["args"], _id=inp_job_ID
                    )
                    job["infiles"].append(inp_file)
                    job["needs"].append(inp_job_ID)
                    job["order"] = max(
                        job["order"], self._jobs[inp_job_ID]["order"] + 1
                    )
                    needs_hash.update(
                        self._jobs[inp_job_ID]["args_json"].encode("utf-8")
                    )
        job["needs_ID"] = needs_hash.hexdigest()

        # Parse output file list, and store whether job is considered final
        job["outfiles"] = []
        if outputs is not None:
            if len(outputs) != len(set(outputs)):
                msg = "'outputs' is not a unique list"
                raise ValueError(msg)
            for outp in outputs:
                job["outfiles"].append(jobdir / outp.format(**args, _id=job_ID))
        job["outfinal"] = final

        # Reolve all input and output file paths
        for key in ("infiles", "outfiles"):
            job[key] = [path.resolve() for path in job[key]]

        # Format and store commands
        cmdargs = dict(args)
        cmdargs["_in"] = [p.name for p in job["infiles"]]
        cmdargs["_out"] = [p.name for p in job["outfiles"]]
        job["cmds"] = [
            [str(arg).format(**cmdargs, **job["resources"]) for arg in cmd]
            for cmd in cmds
        ]

        # Determine full path to executables
        for cmd in job["cmds"]:
            execpath = which(cmd[0])
            if execpath is None:
                msg = f"executable '{cmd[0]}' cannot be found"
                raise ValueError(msg)
            cmd[0] = Path(execpath)

        # Add job to workflow
        self._jobs[job_ID] = job

        # Log progress
        self._log_add_jobs.update()

        return job_ID

    def __getitem__(self, key: Tuple[str, str]) -> Any:
        """
        Retrieve the value of a job argument.
        """
        return self._jobs[key[0]]["args"][key[1]]

    def write(self) -> Path:
        """
        Write workflow files and directories.

        Returns:
            Path: path where workflow was written.
        """

        # Log progress
        self._log_add_jobs.finish()

        # Generate workflow ID as hash of all job JSON representations
        workflow_ID = self._workflow_hash.hexdigest()

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
                    raise ValueError(msg)
                out_final_files.add(out_file.name)

        # Make directory for final output files, and check any existing files
        outfinaldir = self._workdir / "outputs"
        outfinaldir.mkdir(parents=True, exist_ok=True)
        for outfinaldir_file in outfinaldir.iterdir():
            if outfinaldir_file.name in out_final_files:
                msg = f"final output file '{outfinaldir_file.name}' already exists in '{outfinaldir}'"
                raise ValueError(msg)

        log_make_jobs = ProgressLogger(_logger, "wrote %s job directories in workflow")
        for job in self._jobs.values():

            # Make job directory
            job["dir"].mkdir(parents=True, exist_ok=True)

            # Write JSON representation of arguments
            job_args_file = job["dir"] / "job_args.json"
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
                    f += "export {}={}".format(var, quote(val))

                # Write job commands
                for cmd in job["cmds"]:
                    quotedargs = [quote(str(arg)) for arg in cmd]
                    if cmd[0].resolve() in job["infiles"]:
                        quotedargs[0] = "./" + cmd[0].name
                    else:
                        quotedargs[0] = str(cmd[0])
                    f += " ".join(quotedargs)

            log_make_jobs.update()
        log_make_jobs.finish()

        # Write list of all job directories
        all_jobs_file = self._workdir / f"workflow_jobs_{workflow_ID}.txt"
        with FileLineWriter(all_jobs_file) as f:
            for job in self._jobs.values():
                f += job["dir"]

        # Name of file containing directories of any failed jobs
        failedjobfile = self._workdir / f"workflow_failed_jobs_{workflow_ID}.txt"

        # Name of file which workflow will write final state to
        statefile = self._workdir / f"workflow_state_{workflow_ID}.txt"

        # Name of file indicating workflow has been submitted
        submittedfile = self._workdir / f".workflow_submitted_{workflow_ID}.txt"

        # Write top-level submit/queue/cancel control script stubs
        ctrlscripts = dict()
        for ctrlscript in ("submit", "queue", "cancel"):
            ctrlscripts[ctrlscript] = self._workdir / ctrlscript
            with FileLineWriter(ctrlscripts[ctrlscript]) as f:
                f += """
#!/bin/bash
set -e
if test "X$1" != X; then
    echo "usage: $0" >&2
    exit 1
fi
"""

        # Write preambles to top-level control scripts
        with FileLineWriter(ctrlscripts["submit"], append=True) as f:
            f += f"""
if test -f '{statefile}'; then
    state=`cat '{statefile}'`
    if test "X${{state}}" = XSUCCESS; then
        echo "info: workflow {workflow_ID} has already been successfully run"
        exit 1
    fi
    rm -f '{submittedfile}'
fi
if test -f '{submittedfile}'; then
    echo "info: workflow {workflow_ID} has already been submitted"
    exit 1
fi
rm -f '{statefile}'
rm -f '{failedjobfile}'
"""

        # Make batch system script directory
        batchdir = self._workdir / "batch" / workflow_ID
        batchdir.mkdir(parents=True, exist_ok=False)

        # Write batch system scripts
        self._workflow_writer(
            workflowname=f"awghr_{workflow_ID}",
            batchdir=batchdir,
            account=self._account,
            jobs=self._jobs,
            outfinaldir=outfinaldir,
            failedjobfile=failedjobfile,
            statefile=statefile,
            ctrlscripts=ctrlscripts,
        )

        # Write codas to top-level control scripts
        with FileLineWriter(ctrlscripts["submit"], append=True) as f:
            f += f"date > '{submittedfile}'"
            f += f"echo 'info: submitted all jobs in workflow {workflow_ID}'"
        with FileLineWriter(ctrlscripts["cancel"], append=True) as f:
            f += f"echo 'info: cancelled all jobs in workflow {workflow_ID}'"

        # Write top-level wait script
        ctrlscripts["wait"] = self._workdir / "wait"
        with FileLineWriter(ctrlscripts["wait"]) as f:
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
until test -f '{statefile}'; do
    "$@"
    sleep ${{sleepfor}}
done
echo "workflow state: `cat '{statefile}'`"
"""

        # Make top-level control scripts executable
        for ctrlscriptpath in ctrlscripts.values():
            os.chmod(ctrlscriptpath, stat.S_IRUSR | stat.S_IXUSR)

        # Log completion
        _logger.info("workflow has been written")

        return self._workdir
