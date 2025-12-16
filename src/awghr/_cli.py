# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Command-line parser."""

import argparse
import json
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

from ._config import show_config


def _resolved_path(value):
    return Path(value).resolve(strict=True)


def _positive_int(value):
    value = int(value)
    if value <= 0:
        msg = f"'{value}' is not a positive integer"
        raise argparse.ArgumentTypeError(msg)
    return value


def _show_stdouterr(prefix, stdfile, grep_regex, last_n_lines):

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

        # Print lines
        for line in lines:
            print(prefix, line)


def _show_jobs(args):

    # Check for workflows
    job_list_files = list(args.workflow_dir.glob("batch/*/workflow_all_jobs"))
    if len(job_list_files) == 0:
        msg = f"'{args.workflow_dir}' is not a workflow directory"
        raise FileNotFoundError(msg)

    # Get list of jobs to examine
    jobs = set()
    batchdirs = {}
    for job_list_file in job_list_files:

        # If only showing failed jobs, use failed jobs list if possible
        failed_job_list_file = job_list_file.parent / "workflow_failed_jobs"
        if args.state == "FAILED" and failed_job_list_file.is_file():
            use_job_list_file = failed_job_list_file
        else:
            # Otherwise use full jobs list
            use_job_list_file = job_list_file

        # Update list of jobs
        new_jobs = [Path(line) for line in use_job_list_file.read_text().split()]
        jobs.update(new_jobs)
        batchdir = str(use_job_list_file.parent)
        batchdirs.update(dict.fromkeys(new_jobs, batchdir))

    # Load cache
    cache = {}
    cache_file = args.workflow_dir / "jobs" / ".cache"
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
    for job in tqdm(jobs, desc="Cacheing ", disable=None, leave=False):

        # Add job batch IDs to cache
        if job not in cache["batchID"]:
            job_batchID_file = job / "job_batchID"
            if job_batchID_file.is_file():
                job_batchID = job_batchID_file.read_text().strip()
                if job_batchID.startswith("!"):  # Dynamic batch ID

                    # Run command to get batch ID
                    if args.batch_ID_rate > 0:
                        job_state_file = job / "job_state"
                        if job_state_file.is_file():
                            job_state = job_state_file.read_text().strip()
                            if job_state == "RUNNING":
                                job_batchID_cmd = job_batchID[1:]
                                try:
                                    job_batchID = subprocess.check_output(
                                        job_batchID_cmd, shell=True, text=True
                                    ).strip()
                                    if len(job_batchID) > 0:
                                        cache["batchID"][job] = job_batchID
                                except (
                                    subprocess.CalledProcessError
                                ):  # pragma: no cover
                                    pass

                                # Rate limit batch ID commands
                                time.sleep(1.0 / args.batch_ID_rate)

                else:  # Static batch ID
                    cache["batchID"][job] = job_batchID

        # Add job arguments to cache
        if job not in cache["args"]:
            cache["args"][job] = json.load((job / "job_args.json").open())
            cache["sorted_args"][job] = sorted(cache["args"][job].items())
            cache["constant_args"] = None

    # Find constant job arguments, in order to hide them in output
    if cache["constant_args"] is None:
        cache["constant_args"] = set()

        # Only if there is more than one job
        if len(jobs) > 1:

            # Count how many different values each argument has
            job_args_counts = {}
            for job in jobs:
                for arg, val in cache["args"][job].items():
                    if arg not in job_args_counts:
                        job_args_counts[arg] = {}
                    if val not in job_args_counts[arg]:
                        job_args_counts[arg][val] = 0
                    job_args_counts[arg][val] += 1

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
    job_state_counts = defaultdict(int)
    for job in tqdm(jobs, desc="Filtering", disable=None, leave=False):

        # Filter jobs by batch system ID
        if args.batch_ID:  # pragma: no cover
            if args.batch_ID != cache["batchID"].get(job, None):
                continue

        # Filter jobs by arguments
        if args.filter_expr:
            job_args = cache["args"][job]

            # Set any unrecognised names to None
            for name in filter_code.co_names:
                if name not in job_args:
                    job_args[name] = None

            # Execute filter
            if not eval(filter_code, {"__builtins__": {}}, job_args):
                continue

        # Filter jobs by state
        job_state_file = job / "job_state"
        if job_state_file.is_file():
            job_state = job_state_file.read_text().strip()
            job_state_last_modified_cache[job] = datetime.fromtimestamp(
                job_state_file.stat().st_mtime
            )
        else:
            job_state = "PENDING"
        job_state_cache[job] = job_state
        job_state_counts[job_state] += 1
        if args.state != "ALL" and args.state != job_state:
            continue

        filtered_jobs.append(job)

    # Show job, ordered jobs by their arguments
    ordered_jobs = sorted([(cache["sorted_args"][job], job) for job in filtered_jobs])
    len_n = len(str(len(ordered_jobs)))
    for n, (job_args, job) in enumerate(ordered_jobs, start=1):
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
        job_state_str = job_state_cache[job]
        if job in job_state_last_modified_cache:
            job_state_str += job_state_last_modified_cache[job].strftime(
                " %Y-%m-%d %H:%M:%S"
            )
        if job_state_cache[job] == "RUNNING":
            job_state_str += " BATCHID " + cache["batchID"].get(job, "unknown")
        print(prefix.format("STATE"), job_state_str)

        # Show job directory
        if args.batch_dir:
            print(prefix.format("BATCH"), batchdirs[job])

        # Show job directory
        if args.job_dir:
            print(prefix.format("JOB"), job)

        # Show files in job directory
        if args.files:
            for f in sorted(
                job.iterdir(), key=lambda j: (not j.name.startswith("job_"), j)
            ):
                f_size = Quantity(f.stat().st_size, "byte").to_compact()
                print(prefix.format("FILE"), f, "SIZE", format(f_size, "0.1f~"))

        # Show standard output
        if args.output:
            for job_stdout_names in ("job_stdout_pre", "job_stdout", "job_stdout_post"):
                job_stdout = job / job_stdout_names
                if job_stdout.is_file():
                    _show_stdouterr(
                        prefix.format("OUT"),
                        job_stdout,
                        args.grep_regex,
                        args.last_n_lines,
                    )

        # Show standard error
        if args.error:
            for job_stderr_names in ("job_stderr_pre", "job_stderr", "job_stderr_post"):
                job_stderr = job / job_stderr_names
                if job_stderr.is_file():
                    _show_stdouterr(
                        prefix.format("ERR"),
                        job_stderr,
                        args.grep_regex,
                        args.last_n_lines,
                    )

        # Show job usage statistics
        if args.usage:
            usage_regex = "|".join(args.usage)
            job_usage = job / "job_usage"
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


def cli(*argv: str):
    """Command-line parser entry point."""

    # Build command-line argument parser
    parser = argparse.ArgumentParser(
        description="yet another high-throughput workflow manager"
    )
    subparsers = parser.add_subparsers(required=True, help="sub-commands")

    # Add show jobs sub-command
    parser_show_jobs = subparsers.add_parser(
        "jobs", description="show jobs", help="show jobs (default)"
    )
    parser_show_jobs.add_argument(
        "-d",
        "--workflow-dir",
        metavar="DIR",
        type=_resolved_path,
        default=Path.cwd(),
        help="set workflow directory to DIR",
    )
    parser_show_jobs.add_argument(
        "-A",
        "--all",
        dest="state",
        action="store_const",
        const="ALL",
        help="show all jobs",
    )
    parser_show_jobs.add_argument(
        "-P",
        "--pending",
        dest="state",
        action="store_const",
        const="PENDING",
        help="show only pending jobs",
    )
    parser_show_jobs.add_argument(
        "-R",
        "--running",
        dest="state",
        action="store_const",
        const="RUNNING",
        help="show only running jobs",
    )
    parser_show_jobs.add_argument(
        "-S",
        "--success",
        dest="state",
        action="store_const",
        const="SUCCESS",
        help="show only successful jobs",
    )
    parser_show_jobs.add_argument(
        "-K",
        "--skipped",
        dest="state",
        action="store_const",
        const="SUCCESS/SKIPPED",
        help="show only skipped jobs",
    )
    parser_show_jobs.add_argument(
        "-F",
        "--failed",
        dest="state",
        action="store_const",
        const="FAILED",
        help="show only failed jobs (default)",
    )
    parser_show_jobs.set_defaults(state="FAILED")
    parser_show_jobs.add_argument(
        "-i",
        "--batch-ID",
        metavar="ID",
        type=str,
        default=None,
        help="show only job with the given batch system ID",
    )
    parser_show_jobs.add_argument(
        "-f",
        "--filter",
        metavar="EXPR",
        dest="filter_expr",
        type=str,
        default=None,
        help="evaluate EXPR to filter jobs by their arguments",
    )
    parser_show_jobs.add_argument(
        "-b", "--batch-dir", action="store_true", help="show the batch directory"
    )
    parser_show_jobs.add_argument(
        "-j", "--job-dir", action="store_true", help="show the job directory"
    )
    parser_show_jobs.add_argument(
        "-l", "--files", action="store_true", help="show files in job directory"
    )
    parser_show_jobs.add_argument(
        "-o", "--output", action="store_true", help="show standard output"
    )
    parser_show_jobs.add_argument(
        "-e", "--error", action="store_true", help="show standard error"
    )
    parser_show_jobs.add_argument(
        "-g",
        "--grep",
        metavar="REGEX",
        dest="grep_regex",
        type=re.compile,
        action="append",
        default=[],
        help="filter lines of standard output/error through REGEX",
    )
    parser_show_jobs.add_argument(
        "-n",
        "--last",
        metavar="N",
        dest="last_n_lines",
        type=_positive_int,
        default=10,
        help="show the last N lines of standard output/error (default: 10)",
    )
    parser_show_jobs.add_argument(
        "-z",
        "--full",
        dest="last_n_lines",
        action="store_const",
        const=0,
        help="show the full standard output/error",
    )
    parser_show_jobs.add_argument(
        "-u",
        "--usage",
        type=str,
        nargs="*",
        help="show matching lines from job usage statistics",
    )
    parser_show_jobs.add_argument(
        "-@",
        "--get-batch-IDs",
        metavar="RATE",
        dest="batch_ID_rate",
        type=int,
        default=0,
        help="retrieve batch IDs at the given rate (per second)",
    )
    parser_show_jobs.set_defaults(subcommand=_show_jobs)

    # Add show configuration sub-command
    parser_show_config = subparsers.add_parser(
        "config", description="show configuration", help="show configuration"
    )
    parser_show_config.add_argument("--tags", nargs="+", help="assume these tags")
    parser_show_config.add_argument(
        "--batch",
        metavar="BATCH_SYSTEM",
        type=str,
        default=None,
        help="assume this batch system",
    )
    parser_show_config.set_defaults(subcommand=show_config)

    # Parse command line
    parse_argv = list(argv if len(argv) > 0 else sys.argv[1:])
    if len(parse_argv) == 0 or parse_argv[0] not in ("-h", "--help", "jobs", "config"):
        parse_argv.insert(0, "jobs")
    args = parser.parse_args(parse_argv)

    # Execute sub-command, pipe output through a pager
    # - Ignore errors if pipe is broken
    try:
        with subprocess.Popen(
            os.getenv("PAGER", "less -FRSX"),
            shell=True,
            stdin=subprocess.PIPE,
            text=True,
        ) as p:
            with redirect_stdout(p.stdin):
                args.subcommand(args)
                sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):  # pragma: no cover
        p.terminate()
