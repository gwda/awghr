import argparse
import json
import re
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from .config import show_config


def nonnegative_int(value):
    value = int(value)
    if value < 0:
        msg = f"'{value}' is not a nonnegative integer"
        raise argparse.ArgumentTypeError(msg)
    return value


def read_stdouterr(stdfile, grep_regex, last_n_lines):

    # Store lines in deque
    lines = deque()
    max_lines = False

    # Open standard output/error file
    with stdfile.open() as f:

        # Read lines
        for line in f:
            line = line.rstrip()

            # Discard lines not matching regex
            discard_line = False
            for rx in grep_regex:
                if re.search(rx, line) is None:
                    discard_line = True
                    break
            if discard_line:
                continue

            # Add lines to deque, keeping only the `last_n_lines`
            # lines (provided `last_n_lines` > 0)
            if max_lines:
                lines.popleft()
                lines.append(line)
            else:
                lines.append(line)
                if len(lines) == last_n_lines:
                    max_lines = True

    return lines


def show_jobs(args):

    # Check for workflows
    job_lists = list(args.workflow_dir.glob("workflow_jobs_*.txt"))
    if len(job_lists) == 0:
        msg = f"'{args.workflow_dir}' is not a workflow directory"
        raise FileNotFoundError(msg)

    # Get list of jobs and their states
    jobs = []
    job_state_cache = {}
    for job_list in job_lists:

        # If only showing failed jobs, use failed jobs list if possible
        if args.state == "FAILED":
            failed_job_list = job_list.parent / job_list.name.replace(
                "_jobs_", "_failed_jobs_"
            )
            if failed_job_list.is_file():

                # Read list of failed jobs
                with failed_job_list.open() as f:
                    for job in [Path(line.strip()) for line in f]:
                        jobs.append(job)
                        job_state_cache[job] = "FAILED"

                continue

        # Read job state files
        with job_list.open() as f:
            for job in [Path(line.strip()) for line in f]:
                job_state_file = job / "job_state"
                if job_state_file.is_file():
                    job_state = job_state_file.read_text().strip()
                    if args.state == "ALL" or args.state == job_state:
                        jobs.append(job)
                        job_state_last_modified = datetime.fromtimestamp(
                            job_state_file.stat().st_mtime
                        )
                        job_state_cache[job] = (
                            job_state
                            + " "
                            + job_state_last_modified.strftime(
                                "%a, %d %b %Y %H:%M:%S %Z"
                            )
                        )
                else:
                    job_state = "PENDING"
                    if args.state == "ALL" or args.state == job_state:
                        jobs.append(job)
                        job_state_cache[job] = job_state

    if len(jobs) == 0:
        return

    # Cache job arguments
    job_args_cache = {}
    for job in jobs:
        with (job / "job_args.json").open() as f:
            job_args_cache[job] = json.load(f)

    if args.filter_by:

        # Filter jobs
        filtered_jobs = []
        for job in jobs:
            job_args = job_args_cache[job]

            # Compile filter
            filter_code = compile(args.filter_by, "--filter-by", "eval")
            for name in filter_code.co_names:
                if name not in job_args:
                    msg = "--filter-by may only reference job arguments '{}'".format(
                        "', '".join(job_args.keys())
                    )
                    raise NameError(msg)

            # Execute filter
            if eval(filter_code, {"__builtins__": {}}, job_args):
                filtered_jobs.append(job)

    else:
        filtered_jobs = jobs

    # Order jobs by their arguments
    ordered_jobs = [(sorted(job_args_cache[job].items()), job) for job in filtered_jobs]

    # Show jobs
    for n, (job_args, job) in enumerate(ordered_jobs):
        if n > 0:
            print("")

        # Create job prefix
        job_prefix = ",".join([f"{k}={v}" for k, v in job_args])

        # Show job state
        print("JOB {}\tSTATE {}".format(job_prefix, job_state_cache[job]))

        # Show standard output
        if args.output:
            job_stdout = job / "job_stdout"
            if job_stdout.is_file():
                for line in read_stdouterr(
                    job_stdout, args.grep_regex, args.last_n_lines
                ):
                    print("JOB {}\tOUT {}".format(job_prefix, line.rstrip()))

        # Show standard error
        if args.error:
            job_stderr = job / "job_stderr"
            if job_stderr.is_file():
                for line in read_stdouterr(
                    job_stderr, args.grep_regex, args.last_n_lines
                ):
                    print("JOB {}\tERR {}".format(job_prefix, line.rstrip()))

        # Show files in job directory
        if args.files:
            for f in sorted(job.iterdir()):
                if not f.name.startswith("job_"):
                    print("JOB {}\tFILE {}".format(job_prefix, f))


def cli(*argv: str):

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
        "-d", "--workflow-dir", type=Path, default=Path.cwd(), help="workflow directory"
    )
    parser_show_jobs.add_argument(
        "-A",
        "--all-jobs",
        dest="state",
        action="store_const",
        const="ALL",
        help="show all jobs",
    )
    parser_show_jobs.add_argument(
        "-P",
        "--pending-jobs",
        dest="state",
        action="store_const",
        const="PENDING",
        help="show only pending jobs",
    )
    parser_show_jobs.add_argument(
        "-R",
        "--running-jobs",
        dest="state",
        action="store_const",
        const="RUNNING",
        help="show only running jobs",
    )
    parser_show_jobs.add_argument(
        "-S",
        "--success-jobs",
        dest="state",
        action="store_const",
        const="SUCCESS",
        help="show only successful jobs",
    )
    parser_show_jobs.add_argument(
        "-F",
        "--failed-jobs",
        dest="state",
        action="store_const",
        const="FAILED",
        help="show only failed jobs (default)",
    )
    parser_show_jobs.set_defaults(state="FAILED")
    parser_show_jobs.add_argument(
        "-f",
        "--filter-by",
        type=str,
        default=None,
        help="evaluate to filter jobs by their arguments",
    )
    parser_show_jobs.add_argument(
        "-o", "--output", action="store_true", help="show standard output"
    )
    parser_show_jobs.add_argument(
        "-e", "--error", action="store_true", help="show standard error"
    )
    parser_show_jobs.add_argument(
        "-g",
        "--grep-regex",
        type=re.compile,
        action="append",
        default=[],
        help="filter lines of standard output/error through regular expressions",
    )
    parser_show_jobs.add_argument(
        "-n",
        "--last-n-lines",
        type=nonnegative_int,
        default=10,
        help="show the last <n> lines of standard output/error (default: 10; if zero, show all lines)",
    )
    parser_show_jobs.add_argument(
        "-l", "--files", action="store_true", help="show files in job directory"
    )
    parser_show_jobs.set_defaults(subcommand=show_jobs)

    # Add show configuration sub-command
    parser_show_config = subparsers.add_parser(
        "config", description="show configuration", help="show configuration"
    )
    parser_show_config.add_argument("--tags", nargs="+", help="assume these tags")
    parser_show_config.add_argument(
        "--batch", type=str, default=None, help="assume this batch system"
    )
    parser_show_config.set_defaults(subcommand=show_config)

    # Parse command line
    parse_argv = list(argv if len(argv) > 0 else sys.argv[1:])
    if len(parse_argv) == 0 or parse_argv[0] not in ("-h", "--help", "jobs", "config"):
        parse_argv.insert(0, "jobs")
    args = parser.parse_args(parse_argv)

    # Execute sub-command
    args.subcommand(args)
