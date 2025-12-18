# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Local workflows.

Runs jobs on local machine for debugging purposes.
"""

import logging
from typing import Any, Dict, Tuple

from ._util import FileLineWriter, WorkflowWriter, job_dir_from_ID

_logger = logging.getLogger(__name__)


class LocalWorkflowWriter(WorkflowWriter):
    """Write local workflow."""

    # Configuration keys for local workflow
    KEYS: Dict[str, Any] = {}

    def __init__(self, *, batchsyscfg):
        super().__init__(batchsyscfg=batchsyscfg)

    def write_workflow(
        self,
        *,
        ID,
        batchdir,
        workflowname,
        account,
        jobs,
        outfinaldir,
        failedjobfile,
        workstatefile,
        submittedfile,
        skip_exit_code,
    ) -> None:
        """Write workflow."""

        # Write script to run all jobs
        all_jobs_script_file = batchdir / "batch_all_script.sh"
        all_jobs_stdout_file = batchdir / "batch_all_stdout"
        all_jobs_stderr_file = batchdir / "batch_all_stderr"
        all_jobs_pid_file = batchdir / "batch_all_pid"
        with FileLineWriter(all_jobs_script_file) as f:
            f += "#!/bin/bash"

            # Exit on any script errors
            f += "set -e"

            # Redirect standard output and error from script
            f += f"exec >'{all_jobs_stdout_file}' 2>'{all_jobs_stderr_file}'"

            # Save process ID
            # - dash (`-`) is needed to send kill signal to process group
            f += f"echo -$$ > '{all_jobs_pid_file}'"

            # Write shell function fail() for reporting errors
            f += """
            function fail() {
                echo 'FAIL:' "$@" >&2
                exit 1
            }
            """

            # Set trap to write workflow state
            # - if a job is still RUNNING, it clearly didn't
            #   complete successfully, so mark as FAILED
            # - for signals, exit with 128+<signal number>
            f += """
            function trap_workflow_state() {
                local status=$1
                workstate=SUCCESS
            """
            with f.indent(4):
                for job in jobs.values():
                    jobstate_file = job["dir"] / "job_state"
                    f += f"""
                    jobstate=`cat '{jobstate_file}' 2>/dev/null || true`
                    if test "X${{jobstate%%/*}}" != XSUCCESS; then
                        workstate=FAILED
                        if test "X${{jobstate}}" = XRUNNING; then
                            echo FAILED > '{jobstate_file}'
                        fi
                    fi
                    """
            f += f"""
                echo "${{workstate}}" > '{workstatefile}'
                rm -f '{submittedfile}'
                rm -f '{all_jobs_pid_file}'
                exit ${{status}}
            }}
            trap 'trap_workflow_state $?' EXIT
            trap 'trap_workflow_state 143' TERM
            """

            # Ensure list of directories of failed jobs does not exist
            f += f"rm -f '{failedjobfile}'"

            # Run jobs in order
            for n, job in enumerate(jobs.values(), start=1):

                # Run jobs inside a sub-shell
                f += "("
                with f.indent(4):
                    f += f"""echo 'info: running job #{n}/{len(jobs)}: {job["dir"]}'"""

                    # Write job batch ID to file
                    jobbatchID_file = job["dir"] / "job_batchID"
                    f += f"""echo '{n}' > '{jobbatchID_file}'"""

                    # Set trap to write job state file
                    # - for signals, exit with 128+<signal number>
                    jobstate_file = job["dir"] / "job_state"
                    f += f"""
                    function trap_job_state() {{
                        local status=$1
                        if test ${{status}} -eq 0; then
                            echo SUCCESS > '{jobstate_file}'
                            exit 0
                        elif test ${{status}} -eq {skip_exit_code}; then
                            echo SUCCESS/SKIPPED > '{jobstate_file}'
                            exit 0
                        else
                            echo FAILED > '{jobstate_file}'
                            echo '{job["dir"]}' >> '{failedjobfile}'
                            exit ${{status}}
                        fi
                    }}
                    trap 'trap_job_state $?' EXIT
                    trap 'trap_job_state 143' TERM
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
                    jobscratchdir = job_dir_from_ID(batchdir / "job_scratch", job["ID"])
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
                            exit {skip_exit_code}
                        elif test "X${{jobstate}}" != XSUCCESS; then
                            fail 'Prerequisite job {needs_job_ID} was not successful'
                        fi
                        """

                    # Soft-link input files into job scratch directory
                    for infile in job["infiles"]:
                        f += f"test -f '{infile}' || fail 'Missing input file {infile.name}'"
                        f += f"ln -sf '{infile}' './{infile.name}'"

                    # Run job script
                    job_stdout_file = job["dir"] / "job_stdout"
                    job_stderr_file = job["dir"] / "job_stderr"
                    f += f"source ./job_script.sh >'{job_stdout_file}' 2>'{job_stderr_file}'"

                    # Hard-link output files out of job directory
                    for outfile in job["outfiles"] + job["job_script_outfiles"]:
                        f += f"test -f './{outfile.name}' || fail 'Missing output file {outfile.name}'"
                        f += f"ln -f './{outfile.name}' '{outfile}'"
                    if job["outfinal"]:
                        for outfile in job["outfiles"]:
                            outfinaldir_file = outfinaldir / outfile.name
                            f += f"test ! -f '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                            f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                    # End sub-shell
                    f += "exit 0"

                f += ")"

    def get_ctrl_commands(self, *, workflowname, basebatchdir) -> Tuple[str, str, str]:
        """Get top-level control script commands."""

        # Shell function to submit workflows in `basebatchdir` with IDs in $@
        submit = f"""
        submit() {{
            local cmd=
            local ID=
            for ID in $@; do
                if test "X${{cmd}}" != X; then
                    cmd="${{cmd}}; "
                fi
                cmd="${{cmd}}bash '{basebatchdir}/${{ID}}/batch_all_script.sh'"
            done
            setsid bash -c "${{cmd}}" &
        }}
        """

        # Shell function to display all queued jobs from workflows in `basebatchdir` with IDs in $@
        queue = f"""
        queue() {{
            local stdoutfiles=
            local ID=
            for ID in $@; do
                local stdoutfile="{basebatchdir}/${{ID}}/batch_all_stdout"
                if test -f "${{stdoutfile}}"; then
                    stdoutfiles="${{stdoutfiles}} ${{stdoutfile}}"
                fi
            done
            if test "X${{stdoutfiles}}" != X; then
                tail ${{stdoutfiles}}
            fi
        }}
        """

        # Shell function to cancel all jobs from from workflows in `basebatchdir` with IDs in $@
        cancel = f"""
        cancel() {{
            local pids=
            local ID=
            for ID in $@; do
                local pidfile="{basebatchdir}/${{ID}}/batch_all_pid"
                if test -f "${{pidfile}}"; then
                    pid=`cat "${{pidfile}}"`
                    pids="${{pids}} ${{pid}}"
                fi
            done
            if test "X${{pids}}" != X; then
                kill -TERM ${{pids}}
            fi
        }}
        """

        return submit, queue, cancel
