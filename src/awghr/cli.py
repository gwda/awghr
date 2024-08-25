import argparse
import json
import pickle
import re
import shlex
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from .config import show_config
from .util import _ureg


def resolved_path(value):
    return Path(value).resolve(strict=True)


def positive_int(value):
    value = int(value)
    if value <= 0:
        msg = f"'{value}' is not a positive integer"
        raise argparse.ArgumentTypeError(msg)
    return value


def show_stdouterr(prefix, stdfile, grep_regex, last_n_lines):

    # Store lines in deque
    last_lines = deque()
    max_last_lines = False

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

            # If not showing last lines, print straight away
            if last_n_lines == 0:
                print(prefix, line)
                continue

            # Add lines to deque, keeping only the `last_n_lines` lines
            if max_last_lines:
                last_lines.popleft()
                last_lines.append(line)
            else:
                last_lines.append(line)
                if len(last_lines) == last_n_lines:
                    max_last_lines = True

    # Print any last lines
    for line in last_lines:
        print(prefix, line)


def show_jobs(args):

    # Check for workflows
    job_list_files = list(args.workflow_dir.glob("workflow_jobs_*.txt"))
    if len(job_list_files) == 0:
        msg = f"'{args.workflow_dir}' is not a workflow directory"
        raise FileNotFoundError(msg)

    # Get list of jobs to examine
    jobs = []
    for job_list_file in job_list_files:

        # If only showing failed jobs, use failed jobs list if possible
        if args.state == "FAILED":
            failed_job_list_file = job_list_file.parent / job_list_file.name.replace(
                "_state_", "_failed_jobs_"
            )
            if failed_job_list_file.is_file():
                jobs.extend(
                    [Path(line) for line in failed_job_list_file.read_text().split()]
                )
                continue

        # Otherwise use full jobs list
        jobs.extend([Path(line) for line in job_list_file.read_text().split()])

    # Load cache
    cache = {}
    cache_file = args.workflow_dir / ".jobs_cache"
    if cache_file.is_file():
        cache = pickle.load(cache_file.open("rb"))
    if "batchID" not in cache:
        cache["batchID"] = {}
    if "args" not in cache or "sorted_args" not in cache:
        cache["args"] = {}
        cache["sorted_args"] = {}
    if "constant_args" not in cache:
        cache["constant_args"] = None

    # Add information that will not change while workflow is running to cache
    for job in tqdm(jobs, desc="Cacheing ", leave=False):

        # Add job batch IDs to cache
        if job not in cache["batchID"]:
            job_batchID_file = job / "job_batchID"
            if job_batchID_file.is_file():
                cache["batchID"][job] = job_batchID_file.read_text().strip()

        # Add job arguments to cache
        if job not in cache["args"]:
            cache["args"][job] = json.load((job / "job_args.json").open())
            cache["sorted_args"][job] = sorted(cache["args"][job].items())
            cache["constant_args"] = None

    # Find constant job arguments, in order to hide them in output
    if cache["constant_args"] is None:
        job_args_counts = {}
        for job in jobs:
            for arg, val in cache["args"][job].items():
                if arg not in job_args_counts:
                    job_args_counts[arg] = {}
                if val not in job_args_counts[arg]:
                    job_args_counts[arg][val] = 0
                job_args_counts[arg][val] += 1
        cache["constant_args"] = set()
        for arg in job_args_counts:

            # Argument is constant if it only has one value, and all jobs have that value
            if len(job_args_counts[arg]) == 1:
                if list(job_args_counts[arg].items())[0][1] == len(jobs):
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
    for job in tqdm(jobs, desc="Filtering", leave=False):

        # Filter jobs by batch system ID
        if args.batch_ID:  # pragma: no cover
            if args.batch_ID != cache["batchID"][job]:
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
        if args.state != "ALL" and args.state != job_state:
            continue

        filtered_jobs.append(job)

    # Show job, ordered jobs by their arguments
    ordered_jobs = sorted([(cache["sorted_args"][job], job) for job in filtered_jobs])
    for n, (job_args, job) in enumerate(ordered_jobs, start=1):
        if n > 1:
            print("")

        # Create prefix for all printed output
        prefix = f"#{n:<5}" + " {:>5}"

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
        if (
            job_state_cache[job] == "RUNNING" and job in cache["batchID"]
        ):  # pragma: no cover
            job_state_str += " BATCHID " + cache["batchID"][job]
        print(prefix.format("STATE"), job_state_str)

        # Show job directory
        if args.job_dir:
            print(prefix.format("DIR"), job)

        # Show files in job directory
        if args.files:
            for f in sorted(
                job.iterdir(), key=lambda j: (not j.name.startswith("job_"), j)
            ):
                f_size = _ureg.Quantity(f.stat().st_size, "byte").to_compact()
                print(prefix.format("FILE"), f, "SIZE", format(f_size, "~"))

        # Show standard output
        if args.output:
            job_stdout = job / "job_stdout"
            if job_stdout.is_file():
                show_stdouterr(
                    prefix.format("OUT"),
                    job_stdout,
                    args.grep_regex,
                    args.last_n_lines,
                )

        # Show standard error
        if args.error:
            job_stderr = job / "job_stderr"
            if job_stderr.is_file():
                show_stdouterr(
                    prefix.format("ERR"),
                    job_stderr,
                    args.grep_regex,
                    args.last_n_lines,
                )

        sys.stdout.flush()


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
        "-d",
        "--workflow-dir",
        metavar="DIR",
        type=resolved_path,
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
        type=positive_int,
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
    parser_show_jobs.set_defaults(subcommand=show_jobs)

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

    # Execute sub-command
    args.subcommand(args)
