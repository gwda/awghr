# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Manage workflow jobs."""

import argparse
import json
import math
import os
import pickle
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from pint import Quantity
from tqdm import tqdm


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


def filter_stdouterr(stdfile, grep_regex, last_n_lines):

    # Open standard output/error file
    with stdfile.open("rb") as f:

        if last_n_lines > 0:  # pragma: no cover

            # Get preferred chunk size
            try:
                chunk_size = os.fstat(f.fileno()).st_blksize
            except Exception:
                chunk_size = 512

            # Read backwards from end of file to find last lines
            try:

                # Position to read last chunk before end of file
                f.seek(-chunk_size - 1, os.SEEK_END)

                found_lines = 0
                while True:

                    # Read chunk and rewind
                    chunk = f.read(chunk_size)
                    f.seek(-chunk_size, os.SEEK_CUR)

                    # Break if found enough lines
                    found_lines += chunk.count(b"\n")
                    if found_lines > last_n_lines:
                        break

                    # Rewind to previous chunk
                    f.seek(-chunk_size, os.SEEK_CUR)

            except OSError:

                # Go back to start of file
                f.seek(0, os.SEEK_SET)

        # Read lines
        lines = []
        while True:
            line = f.readline()
            if line == b"":
                break
            line = line.decode("utf-8").rstrip()

            # Discard lines not matching regex
            discard_line = False
            for rx in grep_regex:
                if re.search(rx, line) is None:
                    discard_line = True
                    break
            if discard_line:
                continue

            lines.append(line)

        if last_n_lines > 0:

            # Keep only last lines
            lines = lines[-last_n_lines:]

    return lines


def manage_jobs(args, workflow_dir):

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
    cache = {}
    cache_file = workflow_dir / ".manage-cache"
    if cache_file.is_file():

        # Check cache is not older than this source file
        cache_mtime = cache_file.stat().st_mtime
        cli_mtime = Path(__file__).stat().st_mtime
        if cache_mtime > cli_mtime:
            cache = pickle.load(cache_file.open("rb"))

    # Check that cache includes required fields
    if "batchID" not in cache:
        cache["batchID"] = {}
    if "args" not in cache or "sorted_args" not in cache:
        cache["args"] = {}
        cache["sorted_args"] = {}
    if "constant_args" not in cache:
        cache["constant_args"] = None

    # Add information that will not change (once known) while workflow is running to cache
    for job_dir in tqdm(jobs, desc="Cacheing ", disable=None, leave=False):

        # Add job batch IDs to cache
        if job_dir not in cache["batchID"]:
            job_batchID_file = job_dir / "job_batchID"
            if job_batchID_file.is_file():
                job_batchID = job_batchID_file.read_text().strip()
                if job_batchID.startswith("!"):  # Dynamic batch ID

                    # Run command to get batch ID
                    if args.batch_ID_rate > 0:
                        job_state_file = job_dir / "job_state"
                        if job_state_file.is_file():
                            job_state = job_state_file.read_text().strip()
                            if job_state == "RUNNING":
                                job_batchID_cmd = job_batchID[1:]
                                try:
                                    job_batchID = subprocess.check_output(
                                        job_batchID_cmd, shell=True, text=True
                                    ).strip()
                                    if len(job_batchID) > 0:
                                        cache["batchID"][job_dir] = job_batchID
                                except (
                                    subprocess.CalledProcessError
                                ):  # pragma: no cover
                                    pass

                                # Rate limit batch ID commands
                                time.sleep(1.0 / args.batch_ID_rate)

                else:  # Static batch ID
                    cache["batchID"][job_dir] = job_batchID

        # Add job arguments to cache
        if job_dir not in cache["args"]:
            cache["args"][job_dir] = jobs_from_dir[job_dir]["job"]["args"]
            cache["sorted_args"][job_dir] = sorted(cache["args"][job_dir].items())
            cache["constant_args"] = None

    # Find constant job arguments, in order to hide them in output
    if cache["constant_args"] is None:
        cache["constant_args"] = set()

        # Only if there is more than one job
        if len(jobs) > 1:

            # Count how many different values each argument has
            job_args_counts = {}
            for job_dir in jobs:
                for arg, val in cache["args"][job_dir].items():
                    val_str = json.dumps(val, sort_keys=True)
                    if arg not in job_args_counts:
                        job_args_counts[arg] = {}
                    if val_str not in job_args_counts[arg]:
                        job_args_counts[arg][val_str] = 0
                    job_args_counts[arg][val_str] += 1

            # Argument is constant if it only has one value
            for arg in job_args_counts:
                if len(job_args_counts[arg]) == 1:
                    cache["constant_args"].add(arg)

    # Save cache
    pickle.dump(cache, cache_file.open("wb"))

    # Compile filter
    if args.filter_expr:
        filter_code = compile(args.filter_expr, "--filter", "eval")

    # Filter jobs
    filtered_jobs = []
    job_state_cache = {}
    job_state_last_modified_cache = {}
    job_stdouterr = {}
    for job_dir in tqdm(jobs, desc="Filtering", disable=None, leave=False):

        # Filter jobs by batch system ID
        if args.batch_ID:  # pragma: no cover
            if args.batch_ID != cache["batchID"].get(job_dir, None):
                continue

        # Filter jobs by arguments
        if args.filter_expr:
            job_args = cache["args"][job_dir]

            # Set any unrecognised names to None
            for name in filter_code.co_names:
                if name not in job_args:
                    job_args[name] = None

            # Execute filter
            if not eval(filter_code, {"__builtins__": {}}, job_args):
                continue

        # Filter jobs by state
        job_state_file = job_dir / "job_state"
        if job_state_file.is_file():
            job_state = job_state_file.read_text().strip()
            job_state_last_modified_cache[job_dir] = datetime.fromtimestamp(
                job_state_file.stat().st_mtime
            )
        else:
            job_state = "PENDING"
        job_state_cache[job_dir] = job_state
        job_state_counts[job_state] += 1
        if args.state != "ALL" and args.state != job_state:
            continue

        # Filter jobs by standard output/error
        job_stdouterr[job_dir] = {}
        for flag, outerr in ((args.output, "out"), (args.error, "err")):
            job_stdouterr[job_dir][outerr] = []
            if flag:
                for job_stdouterr_suffix in ("_pre", "", "_post"):
                    job_stdouterr_file = (
                        job_dir / f"job_std{outerr}{job_stdouterr_suffix}"
                    )
                    if job_stdouterr_file.is_file():
                        job_stdouterr[job_dir][outerr].extend(
                            filter_stdouterr(
                                job_stdouterr_file,
                                args.grep_regex,
                                args.last_n_lines,
                            )
                        )
                if len(job_stdouterr[job_dir][outerr]) == 0:
                    continue

        filtered_jobs.append(job_dir)

    # Update resources of filtered jobs
    workflows_to_write = set()
    for res_name, res_unit in (
        ("time", "min"),
        ("mem", "MB"),
        ("disk", "MB"),
    ):
        for prefix in ("set", "add"):
            res = f"{res_name}_{res_unit}"
            res_val = getattr(args, f"{prefix}_{res}", 0)
            if res_val != 0:
                for job_dir in filtered_jobs:
                    job = jobs_from_dir[job_dir]["job"]
                    if prefix == "add":
                        job[res] += res_val
                    else:
                        job[res] = res_val
                    workflows_to_write.add(jobs_from_dir[job_dir]["workflow_file"])
    for workflow_file in workflows_to_write:
        json.dump(
            workflows[workflow_file],
            workflow_file.open("w"),
            default=str,
            sort_keys=True,
        )

    # Show job, ordered jobs by their arguments
    ordered_jobs = sorted(
        [(cache["sorted_args"][job_dir], job_dir) for job_dir in filtered_jobs]
    )
    len_n = len(str(len(ordered_jobs)))
    for n, (job_args, job_dir) in enumerate(ordered_jobs, start=1):
        print("")

        # Create prefix for all printed output
        prefix = f"{n:0{len_n}d}" + " {:<5}"

        # Show job arguments
        # - Formatted so that it can be used as a filter to --filter
        job_args_str_unquoted = " and ".join(
            [
                "{}=={}".format(k, repr(v))
                for k, v in job_args
                if k not in cache["constant_args"]
            ]
        )
        if '"' not in job_args_str_unquoted:
            job_args_str_unquoted = job_args_str_unquoted.replace("'", '"')
        job_args_str = shlex.quote(job_args_str_unquoted)
        print(prefix.format("ARGS"), job_args_str)

        # Show job state, date it was last modified, and batch system ID
        job_state_str = job_state_cache[job_dir]
        if job_dir in job_state_last_modified_cache:
            job_state_str += job_state_last_modified_cache[job_dir].strftime(
                " %Y-%m-%d %H:%M:%S"
            )
        if job_state_cache[job_dir] == "RUNNING":
            job_state_str += " BATCHID " + cache["batchID"].get(job_dir, "unknown")
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
            for res_name, res_unit in (
                ("time", "min"),
                ("mem", "MB"),
                ("disk", "MB"),
            ):
                job_res_val = Quantity(
                    job[f"{res_name}_{res_unit}"], res_unit
                ).to_compact()
                print(
                    prefix.format("RES"), res_name.upper(), format(job_res_val, "0.1f~")
                )

        # Show files in job directory
        if args.files:
            for f in sorted(
                job_dir.iterdir(), key=lambda j: (not j.name.startswith("job_"), j)
            ):
                f_size = Quantity(f.stat().st_size, "byte").to_compact()
                print(prefix.format("FILE"), f, "SIZE", format(f_size, "0.1f~"))

        # Show standard output/error
        for outerr in ("out", "err"):
            for line in job_stdouterr[job_dir][outerr]:
                print(prefix.format(outerr.upper()), line)

        # Show job usage statistics
        if args.usage:
            usage_regex = "|".join(args.usage)
            job_usage = job_dir / "job_usage"
            if job_usage.is_file():
                for line in job_usage.read_text().split("\n"):
                    line = line.strip()
                    if re.search(usage_regex, line, re.IGNORECASE) is not None:
                        print(prefix.format("USAGE"), line)

    print("")

    # Create prefix for coda
    prefix = ("-" * len_n) + " {:<5}"

    # Print counts of job states
    for job_state, job_state_count in sorted(job_state_counts.items()):
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
    for c, res_name, res_unit, unit_desc, res_desc in (
        ("T", "time", "min", "time", "wall time"),
        ("M", "mem", "MB", "size", "memory"),
        ("D", "disk", "MB", "size", "temporary disk space"),
    ):
        for p, prefix, verb in (("-", "set", "set"), ("+", "add", "add to")):
            parser.add_argument(
                f"{p}{c}",
                f"{p}{p}{res_name}",
                metavar=unit_desc.upper(),
                dest=f"{prefix}_{res_name}_{res_unit}",
                type=quantity_with_unit(res_unit, unit_desc),
                default=0,
                help=f"{verb} the requested {res_desc} of the selected jobs",
            )

    # Parse command line
    args = parser.parse_args(argv if len(argv) > 0 else sys.argv[1:])

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
                manage_jobs(args, workflow_dir)
                sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):  # pragma: no cover
        proc.terminate()


if __name__ == "__main__":
    cli()
