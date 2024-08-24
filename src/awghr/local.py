import logging
from typing import Any, Dict

from .util import FileLineWriter, job_dir_from_ID

_logger = logging.getLogger(__name__)


# Local workflow: run jobs on local machine for debugging purposes
class LocalWorkflowWriter:

    # Configuration keys for local workflow
    KEYS: Dict[str, Any] = {}

    def __init__(self, batchsyscfg):

        # No configuration needed
        pass

    def __call__(
        self,
        *,
        workflowname,
        batchdir,
        account,
        jobs,
        outfinaldir,
        failedjobfile,
        workstatefile,
        submittedfile,
        ctrlscripts,
    ):

        # Write script to run all jobs
        all_jobs_script_file = batchdir / "run_all_jobs.sh"
        all_jobs_stdout_file = batchdir / "run_all_jobs_stdout"
        all_jobs_stderr_file = batchdir / "run_all_jobs_stderr"
        all_jobs_pid_file = batchdir / "run_all_jobs_pid"
        with FileLineWriter(all_jobs_script_file) as f:
            f += "#!/bin/bash"

            # Exit on any errors
            f += "set -e"

            # Redirect standard output and error from script
            f += f"exec >'{all_jobs_stdout_file}' 2>'{all_jobs_stderr_file}'"

            # Save process ID
            f += f"echo $$ > '{all_jobs_pid_file}'"

            # Write shell function fail() for reporting errors
            f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
"""

            # Set trap to write workflow state
            f += """
function trap_workflow_state() {
    local status=$?
    workstate=SUCCESS
"""
            for job in jobs.values():
                jobstate_file = job["dir"] / "job_state"
                f += f"""
    jobstate=`cat '{jobstate_file}' 2>/dev/null || true`
    if test "X${{jobstate%%/*}}" != XSUCCESS; then
        workstate=FAILED
    fi
"""
            f += f"""
    echo "${{workstate}}" > '{workstatefile}'
    rm -f '{submittedfile}'
    rm -f '{all_jobs_pid_file}'
    exit ${{status}}
"""
            f += """
}
trap trap_workflow_state EXIT TERM
"""

            # Ensure list of directories of failed jobs does not exist
            f += f"rm -f '{failedjobfile}'"

            # Run jobs in order
            for n, job in enumerate(jobs.values(), start=1):

                # Run jobs inside a sub-shell
                f += "("
                f += f"""echo 'info: running job #{n}/{len(jobs)}: {job["dir"]}'"""

                # Set trap to write job state file
                jobstate_file = job["dir"] / "job_state"
                f += f"""
function trap_job_state() {{
    local status=$?
    if test ${{status}} -eq 0; then
        echo SUCCESS > '{jobstate_file}'
        exit 0
    elif test ${{status}} -eq 77; then
        echo SUCCESS/SKIPPED > '{jobstate_file}'
        exit 0
    else
        echo FAILED > '{jobstate_file}'
        echo '{job["dir"]}' >> '{failedjobfile}'
        exit ${{status}}
    fi
}}
trap trap_job_state EXIT TERM
"""

                # Check if job has already been run successfully
                f += f"""
jobstate=`cat '{jobstate_file}' 2>/dev/null || true`
if test "X${{jobstate%%/*}}" = XSUCCESS; then
    exit 0
fi
echo RUNNING > '{jobstate_file}'
"""

                # Change to job scratch directory
                jobscratchdir = job_dir_from_ID(batchdir / "tmp", job["ID"])
                f += f"""
mkdir -p '{jobscratchdir}'
cd '{jobscratchdir}'
"""

                # Check that all prerequisite jobs were successfully run
                for needs_job_ID in job["needs"]:

                    # Path to file indicating job state
                    needs_jobstate_file = jobs[needs_job_ID]["dir"] / "job_state"

                    # Test that job was successful, or skip if it was skipped
                    f += f"""
jobstate=`cat '{needs_jobstate_file}' 2>/dev/null || true`
if test "X${{jobstate}}" = XSUCCESS/SKIPPED; then
    exit 77
elif test "X${{jobstate}}" != XSUCCESS; then
    fail 'Prerequisite job {needs_job_ID} was not successful'
fi
"""

                # Soft-link input files into job scratch directory
                for infile in job["infiles"]:
                    f += (
                        f"test -f '{infile}' || fail 'Missing input file {infile.name}'"
                    )
                    f += f"ln -sf '{infile}' './{infile.name}'"

                # Run job script
                job_stdout_file = job["dir"] / "job_stdout"
                job_stderr_file = job["dir"] / "job_stderr"
                f += (
                    f"source ./job_script.sh >'{job_stdout_file}' 2>'{job_stderr_file}'"
                )

                # Hard-link output files out of job directory
                for outfile in job["outfiles"]:
                    f += f"test -f './{outfile.name}' || fail 'Missing output file {outfile.name}'"
                    f += f"ln -f './{outfile.name}' '{outfile}'"
                    if job["outfinal"]:
                        outfinaldir_file = outfinaldir / outfile.name
                        f += f"test ! -f '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                        f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                # End sub-shell
                f += "exit 0"
                f += ")"

        # Write commands to top-level control scripts
        ctrlscripts[
            "submit"
        ] += f"""
bash '{all_jobs_script_file}' &
"""
        ctrlscripts[
            "queue"
        ] += f"""
test ! -f '{all_jobs_stdout_file}' || tail -n 1 '{all_jobs_stdout_file}'
"""
        ctrlscripts[
            "cancel"
        ] += f"""
pid=`cat '{all_jobs_pid_file}'`
test "X${{pid}}" = X || kill -TERM "${{pid}}"
"""
