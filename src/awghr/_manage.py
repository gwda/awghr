# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Manage workflow jobs."""

import argparse
import asyncio
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent

from pint import Quantity
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio

from ._util import LOGGER, DictCache, write_atomic


def positive_int(value):
    value = int(value)
    if value <= 0:
        msg = f"'{value}' is not a positive integer"
        raise argparse.ArgumentTypeError(msg)
    return value


def quantity_with_unit(unit, unit_desc):
    def _quantity_magnitude(value):
        try:
            return int(math.ceil(Quantity(value).to(unit).magnitude))
        except Exception:
            msg = f"'{value}' is not a value with units of {unit_desc}"
            raise argparse.ArgumentTypeError(msg)

    return _quantity_magnitude


def format_quantity(qty):
    return format(qty.to_compact(), "0.1f~")


def format_time_quantity(qty):
    return str(timedelta(seconds=qty.to("sec").magnitude))


async def read_job_file(
    filepath,
    last_n_lines,
    filetext_if_not_exist,
    filetext_if_error,
    *,
    semaphore,
    timeout,
):

    # Reading job files on cluster file systems may be impacted by servers
    # falling over, being I/O overloaded, etc. This could result in any process
    # trying to access files ending up in an unkillable D state. To try to avoid
    # this, this function runs a shell script which acquries all the job file
    # information needed: file existence, modification time, and contents. Since
    # the script is run in a child process, if it does end up in a D state, the
    # parent process can at worst abandon the child process and continue.

    async with semaphore:

        # Build shell script
        script = dedent(
            """
            if test -r '{filepath}'; then
                ( stat -c %Y '{filepath}' || echo 0 ) 2>/dev/null
                {cat_cmd} '{filepath}'
            else
                echo -1
            fi
            """
        ).format(
            filepath=filepath,
            cat_cmd=f"tail -n {last_n_lines}" if last_n_lines > 0 else "cat",
        )

        # Run shell script
        proc = await asyncio.create_subprocess_shell(
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:  # pragma: no cover
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            return filetext_if_error, None
        finally:
            if proc.returncode is None:  # pragma: no cover
                proc.terminate()
                await proc.wait()
        if proc.returncode != 0:  # pragma: no cover
            return filetext_if_error, None

        # Parse shell script output
        filemtime_text, filetext = stdout.decode().split("\n", maxsplit=1)
        filemtime_int = int(filemtime_text.strip())
        if filemtime_int < 0:
            return filetext_if_not_exist, None
        filemtime = datetime.fromtimestamp(filemtime_int)
        filetext = filetext.strip()

        return filetext, filemtime


async def manage_jobs(args, workflow_dir):

    # Check for workflows
    baseworkflowdir = workflow_dir / "workflows"
    if baseworkflowdir.is_dir():
        workflowdirs = [d for d in baseworkflowdir.iterdir() if d.is_dir()]
    else:
        workflowdirs = []
    if not workflowdirs:
        msg = f"'{workflow_dir}' is not a workflow directory"
        raise FileNotFoundError(msg)

    # Load workflow descriptions, map job directories to job and workflow
    jobs_from_dir = {}
    workflows = {}
    for d in workflowdirs:
        workflow_file = d / "workflow.json"
        workflows[workflow_file] = json.load(workflow_file.open("r"))
        for job in workflows[workflow_file]["jobs"].values():
            jobs_from_dir[Path(job["dir"])] = dict(job=job, workflow_file=workflow_file)

    # Get list of jobs to examine
    jobs = set()
    job_state_counts = defaultdict(int)
    job_workflow_dirs = {}
    for d in workflowdirs:

        # Read workflow state
        workstatefile = d / "state"
        if workstatefile.is_file():
            workstate = workstatefile.read_text().strip()
        else:
            workstate = None
        if workstate == "SUCCESS":

            # Just count number of successful jobs
            use_job_list_file = d / "all_jobs"
            successful_jobs = use_job_list_file.read_text().split()
            job_state_counts["SUCCESS"] += len(successful_jobs)

        else:

            # If only showing failed jobs, use failed jobs list if possible
            failed_job_list_file = d / "failed_jobs"
            if args.state == "FAILED" and failed_job_list_file.is_file():
                use_job_list_file = failed_job_list_file
            else:
                # Otherwise use full jobs list
                use_job_list_file = d / "all_jobs"

            # Update list of jobs
            new_jobs = [Path(line) for line in use_job_list_file.read_text().split()]
            jobs.update(new_jobs)
            wflowdir = str(d)
            job_workflow_dirs.update(dict.fromkeys(new_jobs, wflowdir))

    # Load cache
    with DictCache(workflow_dir / ".manage-cache", __file__) as cache:

        # Check that cache includes required fields
        if "state" not in cache:
            cache["state"] = {}
        if "batchID" not in cache:
            cache["batchID"] = {}
        if "args" not in cache or "sorted_args" not in cache:
            cache["args"] = {}
            cache["sorted_args"] = {}

        # Build list of files to read
        read_job_file_args_list = []
        for job_dir in jobs:

            # Read job state
            if job_dir not in cache["state"]:
                job_state_file = job_dir / "job_state"
                read_job_file_args_list.append(
                    (job_state_file, 0, "PENDING", "FAILED/could not read job state")
                )

            # Read job batch ID
            if job_dir not in cache["batchID"]:
                job_batchID_file = job_dir / "job_batchID"
                read_job_file_args_list.append(
                    (
                        job_batchID_file,
                        0,
                        "UNKNOWN/job batch ID not present",
                        "UNKNOWN/could not read job batch ID",
                    )
                )

            # Read job standard output/error
            for flag, outerr in ((args.output, "out"), (args.error, "err")):
                if flag:
                    for outerr_suffix in ("_pre", "", "_post"):
                        job_stdouterr_file = job_dir / f"job_std{outerr}{outerr_suffix}"
                        read_job_file_args_list.append(
                            (
                                job_stdouterr_file,
                                args.last_n_lines,
                                None,
                                f"ERROR/could not read {job_stdouterr_file.name}",
                            )
                        )

            # Read job usage statistics
            if args.usage:
                job_usage_file = job_dir / "job_usage"
                read_job_file_args_list.append((job_usage_file, 0, "", ""))

        # Limit number of concurrent job file reading tasks
        job_files_semaphore = asyncio.Semaphore(args.concurrent)

        # Build list of tasks
        job_files_tasks = [
            read_job_file(
                *read_job_file_args, semaphore=job_files_semaphore, timeout=args.timeout
            )
            for read_job_file_args in read_job_file_args_list
        ]

        # Run tasks concurrently
        job_files_results = await tqdm_asyncio.gather(
            *job_files_tasks, desc="Reading job files ", disable=None, leave=False
        )
        job_files = {
            read_job_file_args[0]: job_file_result
            for read_job_file_args, job_file_result in zip(
                read_job_file_args_list, job_files_results
            )
        }

        # Compile filter
        if args.filter_expr:
            filter_code = compile(args.filter_expr, "--filter", "eval")

        # Process jobs
        job_state_cache = {}
        job_state_last_modified_cache = {}
        job_stdouterr_lines = {}
        filtered_jobs = []
        for job_dir in tqdm(jobs, desc="Processing jobs ", disable=None, leave=False):

            # Get job state
            try:
                job_state = cache["state"][job_dir]
                job_state_mtime = None
            except KeyError:
                job_state_file = job_dir / "job_state"
                job_state, job_state_mtime = job_files[job_state_file]
            job_state_counts[job_state] += 1

            # Add job state to temporary cache; for successful jobs, add to persistent cache
            job_state_cache[job_dir] = job_state
            if job_state_mtime is not None:
                job_state_last_modified_cache[job_dir] = job_state_mtime
            if job_state == "SUCCESS":
                cache["state"][job_dir] = job_state

            # Filter jobs by state
            if args.state != "ALL" and not job_state.startswith(args.state):
                continue

            if job_state == "RUNNING":

                # Get job batch ID
                try:
                    job_batchID = cache["batchID"][job_dir]
                except KeyError:
                    job_batchID_file = job_dir / "job_batchID"
                    job_batchID, _ = job_files[job_batchID_file]

                # If dynamic batch ID, run command to retrieve it
                if job_batchID.startswith("!"):
                    if args.batch_ID_rate == 0:  # pragma: no cover
                        job_batchID = "UNKNOWN/not retrieving job batch ID"
                    else:
                        job_batchID_cmd = job_batchID[1:]
                        try:
                            res = subprocess.run(
                                job_batchID_cmd,
                                shell=True,
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=args.timeout,
                            )
                            batchID = res.stdout.strip()
                            if len(batchID) > 0:
                                job_batchID = batchID
                            else:
                                # Job may not exist yet, try again later
                                job_batchID = None
                        except subprocess.CalledProcessError:  # pragma: no cover
                            # Job may not exist yet, try again later
                            job_batchID = None
                        except subprocess.TimeoutExpired:  # pragma: no cover
                            job_batchID = "UNKNOWN/could not retrieve job batch ID"

                        # Rate limit batch ID commands
                        time.sleep(1.0 / args.batch_ID_rate)

                # Add job batch IDs to persistent cache
                if job_batchID is not None:
                    cache["batchID"][job_dir] = job_batchID

                # Filter jobs by batch system ID
                if (
                    args.batch_ID is not None and args.batch_ID != job_batchID
                ):  # pragma: no cover
                    continue

            # Get job arguments
            try:
                job_args = cache["args"][job_dir]
            except KeyError:
                job_args = jobs_from_dir[job_dir]["job"]["args"]
                cache["args"][job_dir] = job_args
                cache["sorted_args"][job_dir] = sorted(job_args.items())

            # Filter jobs by arguments
            if args.filter_expr:

                # Set any unrecognised names to None
                for name in filter_code.co_names:
                    if name not in job_args:
                        job_args[name] = None

                # Execute filter
                if not eval(filter_code, {"__builtins__": {}}, job_args):
                    continue

            # Get job standard output/error
            job_stdouterr_nlines = 0
            for flag, outerr in ((args.output, "out"), (args.error, "err")):
                if flag:

                    # Collect standard output/error lines
                    lines = []
                    for outerr_suffix in ("_pre", "", "_post"):
                        job_stdouterr_file = job_dir / f"job_std{outerr}{outerr_suffix}"
                        if job_stdouterr_file in job_files:
                            job_stdouterr, _ = job_files[job_stdouterr_file]
                            if job_stdouterr is not None:
                                lines.extend(job_stdouterr.split("\n"))

                    # Discard standard output/error lines not matching regex
                    for rx in args.grep_regex:
                        lines = [
                            line
                            for line in lines
                            if line.startswith("ERROR/")
                            or re.search(rx, line) is not None
                        ]

                    # Save standard output/error lines
                    job_stdouterr_lines[(job_dir, outerr)] = lines
                    job_stdouterr_nlines += len(lines)

            # Filter jobs by standard output/error
            if (
                (args.output or args.error)
                and args.grep_regex
                and job_stdouterr_nlines == 0
            ):
                continue

            # Add to filtered jobs
            filtered_jobs.append(job_dir)

        # Update resources of filtered jobs
        workflows_to_write = set()
        for job_dir in filtered_jobs:

            # Iterate over resources, update job status with changes
            job_state = job_state_cache[job_dir]
            job_state_counts[job_state] -= 1
            for res_name, res_unit in (
                ("time", "min"),
                ("mem", "MB"),
                ("disk", "MB"),
            ):
                for prefix in ("set", "add"):
                    res = f"{res_name}_{res_unit}"
                    res_val = getattr(args, f"{prefix}_{res}", 0)
                    if res_val != 0:
                        job = jobs_from_dir[job_dir]["job"]
                        if prefix == "add":
                            job[res] += res_val
                            job_state += f"/{res_name}+={res_val}{res_unit}"
                        else:
                            job[res] = res_val
                            job_state += f"/{res_name}={res_val}{res_unit}"
                        workflows_to_write.add(jobs_from_dir[job_dir]["workflow_file"])

            # Write updated job status to cache, file
            job_state_counts[job_state] += 1
            if job_state_cache[job_dir] != job_state:
                job_state_cache[job_dir] = job_state

                # Only write to file if job status has been updated,
                # to avoid interference with other processes
                job_state_file = job_dir / "job_state"
                write_atomic(job_state, job_state_file)

        # Write workflows with updated resources
        for workflow_file in workflows_to_write:
            json.dump(
                workflows[workflow_file],
                workflow_file.open("w"),
                default=str,
                sort_keys=True,
            )

        # Order filtered jobs by their arguments
        ordered_jobs = sorted(
            [(cache["sorted_args"][job_dir], job_dir) for job_dir in filtered_jobs]
        )

        # Show filtered jobs
        len_n = len(str(len(ordered_jobs)))
        for n, (job_args, job_dir) in enumerate(ordered_jobs, start=1):
            print("")

            # Create prefix for all printed output
            prefix = f"{n:0{len_n}d}" + " {:<5}"

            # Show job arguments
            # - Formatted so that it can be used as a filter to --filter
            job_args_str_unquoted = " and ".join(
                ["{}=={}".format(k, repr(v)) for k, v in job_args]
            )
            if '"' not in job_args_str_unquoted:
                job_args_str_unquoted = job_args_str_unquoted.replace("'", '"')
            job_args_str = shlex.quote(job_args_str_unquoted)
            print(prefix.format("ARGS"), job_args_str)

            # Show job state, date it was last modified, and batch system ID
            job_state = job_state_cache[job_dir]
            job_state_str = job_state
            if job_dir in job_state_last_modified_cache:
                job_state_str += job_state_last_modified_cache[job_dir].strftime(
                    " %Y-%m-%d %H:%M:%S"
                )
            if job_state == "RUNNING":
                job_state_str += " BATCHID " + cache["batchID"].get(job_dir, "UNKNOWN")
            print(prefix.format("STATE"), job_state_str)

            # Show workflows/ directory
            if args.workflows_dir:
                print(prefix.format("WFLOW"), job_workflow_dirs[job_dir])

            # Show job directory
            if args.job_dir:
                print(prefix.format("JOB"), job_dir)

            # Show job resources
            if args.resources:
                job = jobs_from_dir[job_dir]["job"]
                for res_name, res_unit, res_fmt in (
                    ("time", "min", format_time_quantity),
                    ("mem", "MB", format_quantity),
                    ("disk", "MB", format_quantity),
                ):
                    job_res_val = Quantity(job[f"{res_name}_{res_unit}"], res_unit)
                    print(prefix.format("RES"), res_name.upper(), res_fmt(job_res_val))

            # Show files in job directory
            if args.files:
                for f in sorted(
                    job_dir.iterdir(), key=lambda j: (not j.name.startswith("job_"), j)
                ):
                    f_size = Quantity(f.stat().st_size, "byte")
                    print(prefix.format("FILE"), f, "SIZE", format_quantity(f_size))

            # Show standard output/error
            for outerr in ("out", "err"):
                if (job_dir, outerr) in job_stdouterr_lines:
                    for line in job_stdouterr_lines[(job_dir, outerr)]:
                        print(prefix.format(outerr.upper()), line)

            # Show job usage statistics
            if args.usage:
                usage_regex = "|".join(args.usage)
                job_usage_file = job_dir / "job_usage"
                job_usage, _ = job_files[job_usage_file]
                for line in job_usage.split("\n"):
                    line = line.strip()
                    if re.search(usage_regex, line, re.IGNORECASE) is not None:
                        print(prefix.format("USAGE"), line)

        print("")

        # Create prefix for coda
        prefix = ("-" * len_n) + " {:<5}"

        # Print counts of job states
        for job_state, job_state_count in sorted(job_state_counts.items()):
            if job_state_count > 0:
                print(prefix.format("STATE"), f"{job_state_count:{len_n}d}", job_state)

        # Print date
        print(prefix.format("DATE"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def cli(*argv: str, workflow_dir: Path = Path.cwd()):
    """Command-line interface: manage jobs."""

    # Build command-line argument parser
    parser = argparse.ArgumentParser(
        prog="manage", description="manage jobs", prefix_chars="-+"
    )
    parser.add_argument(
        "-A",
        "--all",
        dest="state",
        action="store_const",
        const="ALL",
        help="show all jobs",
    )
    parser.add_argument(
        "-P",
        "--pending",
        dest="state",
        action="store_const",
        const="PENDING",
        help="show only pending jobs",
    )
    parser.add_argument(
        "-R",
        "--running",
        dest="state",
        action="store_const",
        const="RUNNING",
        help="show only running jobs",
    )
    parser.add_argument(
        "-S",
        "--success",
        dest="state",
        action="store_const",
        const="SUCCESS",
        help="show only successful jobs",
    )
    parser.add_argument(
        "-K",
        "--skipped",
        dest="state",
        action="store_const",
        const="SUCCESS/SKIPPED",
        help="show only skipped jobs",
    )
    parser.add_argument(
        "-F",
        "--failed",
        dest="state",
        action="store_const",
        const="FAILED",
        help="show only failed jobs (default)",
    )
    parser.set_defaults(state="FAILED")
    parser.add_argument(
        "-Y",
        "--summary",
        dest="state",
        action="store_const",
        const="summary",
        help="show only summary",
    )
    parser.add_argument(
        "-i",
        "--batch-ID",
        metavar="ID",
        type=str,
        default=None,
        help="show only job with the given batch system ID",
    )
    parser.add_argument(
        "-f",
        "--filter",
        metavar="EXPR",
        dest="filter_expr",
        type=str,
        default=None,
        help="evaluate EXPR to filter jobs by their arguments",
    )
    parser.add_argument(
        "-w",
        "--workflows-dir",
        action="store_true",
        help="show the workflows/ directory",
    )
    parser.add_argument(
        "-j", "--job-dir", action="store_true", help="show the job directory"
    )
    parser.add_argument(
        "-r", "--resources", action="store_true", help="show job resources"
    )
    parser.add_argument(
        "-l", "--files", action="store_true", help="show files in job directory"
    )
    parser.add_argument(
        "-o", "--output", action="store_true", help="show standard output"
    )
    parser.add_argument(
        "-e", "--error", action="store_true", help="show standard error"
    )
    parser.add_argument(
        "-g",
        "--grep",
        metavar="REGEX",
        dest="grep_regex",
        type=re.compile,
        action="append",
        default=[],
        help="filter lines of standard output/error through REGEX",
    )
    parser.add_argument(
        "-n",
        "--last",
        metavar="N",
        dest="last_n_lines",
        type=positive_int,
        default=10,
        help="show the last N lines of standard output/error (default: 10)",
    )
    parser.add_argument(
        "-z",
        "--full",
        dest="last_n_lines",
        action="store_const",
        const=0,
        help="show the full standard output/error",
    )
    parser.add_argument(
        "-u",
        "--usage",
        type=str,
        nargs="*",
        help="show matching lines from job usage statistics",
    )
    parser.add_argument(
        "-@",
        "--get-batch-IDs",
        metavar="RATE",
        dest="batch_ID_rate",
        type=int,
        default=0,
        help="retrieve batch IDs at the given rate (per second)",
    )
    resource_args = []
    for c, res_name, res_unit, unit_desc, res_desc in (
        ("T", "time", "min", "time", "wall time"),
        ("M", "mem", "MB", "size", "memory"),
        ("D", "disk", "MB", "size", "temporary disk space"),
    ):
        for p, prefix, verb in (("-", "set", "set"), ("+", "add", "add to")):
            short_arg = f"{p}{c}"
            long_arg = f"{p}{p}{res_name}"
            arg_name = f"{prefix}_{res_name}_{res_unit}"
            parser.add_argument(
                short_arg,
                long_arg,
                metavar=unit_desc.upper(),
                dest=arg_name,
                type=quantity_with_unit(res_unit, unit_desc),
                default=0,
                help=f"{verb} the requested {res_desc} of the selected jobs",
            )
            resource_args.append((long_arg, arg_name))
    parser.add_argument(
        "--concurrent", type=int, default=8, help="limit on concurrent I/O subprocesses"
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=15,
        help="timeout (in seconds) for subprocesses",
    )

    # Parse command line
    args = parser.parse_args(argv if len(argv) > 0 else sys.argv[1:])

    # Check arguments
    resource_args_used = [
        long_arg for long_arg, arg_name in resource_args if getattr(args, arg_name) != 0
    ]
    if resource_args_used and args.state != "FAILED":
        LOGGER.critical(
            "argument(s) %s can only be used to update resources of FAILED jobs",
            ", ".join(resource_args_used),
        )
        sys.exit(1)

    # Run command, pipe output through a pager
    # - Ignore errors if pipe is broken
    try:
        with subprocess.Popen(
            os.getenv("PAGER", "less -FRSX"),
            shell=True,
            stdin=subprocess.PIPE,
            text=True,
        ) as proc:
            with redirect_stdout(proc.stdin):
                asyncio.run(manage_jobs(args, workflow_dir))
                sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):  # pragma: no cover
        proc.terminate()


if __name__ == "__main__":
    cli()
