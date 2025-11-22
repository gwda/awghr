# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""HTCondor workflows."""

import logging
from typing import Any, Dict, Optional, Tuple

from ._util import FileLineWriter, ProgressLogger, WorkflowException, WorkflowWriter

_logger = logging.getLogger(__name__)


class HTCondorWorkflowWriter(WorkflowWriter):
    """Write HTCondor workflow."""

    # HTCondor-specific configuration keys
    KEYS: Dict[str, Any] = {
        "universe": "vanilla",
        "account_user": "default",
        "submit_cmd_account": "accounting_group",
        "submit_cmd_account_user": "accounting_group_user",
        "submit_opt_list": [],
    }

    def __init__(self, *, batchsyscfg):
        """Initialise workflow writer."""

        super().__init__(batchsyscfg=batchsyscfg)

        # Store default universe
        self.universe = batchsyscfg["universe"]

        # Store default user name associated with account
        if batchsyscfg["account_user"] == "default":
            self.account_user = None
        else:  # pragma: no cover
            self.account_user = batchsyscfg["account_user"]

        # Store name of command used to add account and associated user to HTCondor submit file
        self.account_cmd = batchsyscfg["submit_cmd_account"]
        self.account_user_cmd = batchsyscfg["submit_cmd_account_user"]

        # Store default HTCondor job submit file options
        self.default_submit_opt = {}
        for opt_str in batchsyscfg["submit_opt_list"]:
            opt, val = (s.strip() for s in opt_str.split("=", maxsplit=1))
            self.default_submit_opt[opt.upper()] = val

    def _write_sub(self, account, sub_file, sub):
        """Write HTCondor submit file."""
        sub = sub.copy()

        # Add accounting entries
        sub[self.account_cmd.upper()] = account
        if self.account_user is not None:  # pragma: no cover
            sub[self.account_user_cmd.upper()] = self.account_user

        # Write submit file
        with FileLineWriter(sub_file) as f:
            for sub_opt, sub_val in sub.items():
                if sub_val is not None:
                    f += f"{sub_opt} = {sub_val}"
            f += "queue"

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
    ) -> None:
        """Write workflow."""

        # HTCondor job log
        job_log = batchdir / "batch_condor.log"

        # Write HTCondor DAG input file
        dag_file = batchdir / "batch_condor.dag"
        with FileLineWriter(dag_file) as F:

            # Write jobs
            for job in ProgressLogger(
                jobs.values(), logger=_logger, msg="wrote %s jobs in workflow"
            ):

                # Job batch ID file
                jobbatchID_file = job["dir"] / "job_batchID"

                # Job state file
                jobstate_file = job["dir"] / "job_state"

                # Write job pre script
                job_pre_file = job["dir"] / "job_condor_pre.sh"
                with FileLineWriter(job_pre_file) as f:

                    # Exit on any script errors
                    f += "set -e"

                    # Capture standard output and error
                    job_pre_stdout_file = job["dir"] / "job_stdout_pre"
                    job_pre_stderr_file = job["dir"] / "job_stderr_pre"
                    f += f"exec >{job_pre_stdout_file} 2>{job_pre_stderr_file}"

                    # Write shell function fail() for reporting errors
                    f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
"""

                    # Check that all prerequisite jobs were not skipped
                    for needs_job_ID in job["needs"]:

                        # Path to file indicating job state
                        needs_jobstate_file = jobs[needs_job_ID]["dir"] / "job_state"

                        # Skip job if prerequisite job was skipped
                        f += f"""
jobstate=`cat '{needs_jobstate_file}' 2>/dev/null || true`
if test "X${{jobstate}}" = XSUCCESS/SKIPPED; then
    echo SUCCESS/SKIPPED > '{jobstate_file}'
    exit 77
fi
"""

                    # Write job batch ID file
                    f += f"""
echo UNKNOWN > '{jobbatchID_file}'
"""

                    # Write job state file
                    f += f"""
echo RUNNING > '{jobstate_file}'
"""

                    # Remove standard output and error from any previous job
                    job_stdout_file = job["dir"] / "job_stdout"
                    job_stderr_file = job["dir"] / "job_stderr"
                    f += f"rm -f '{job_stdout_file}' '{job_stderr_file}'"

                # Write job post script
                job_post_file = job["dir"] / "job_condor_post.sh"
                with FileLineWriter(job_post_file) as f:

                    # Exit on any script errors
                    f += "set -e"

                    # Capture standard output and error
                    job_post_stdout_file = job["dir"] / "job_stdout_post"
                    job_post_stderr_file = job["dir"] / "job_stderr_post"
                    f += f"exec >{job_post_stdout_file} 2>{job_post_stderr_file}"

                    # Write shell function fail() for reporting errors
                    f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
"""

                    # Set trap to write job state file
                    # - for signals, exit with 128+<signal number>
                    f += f"""
function trap_state() {{
    local status=$1
    if test ${{status}} -eq 0; then
        echo SUCCESS > '{jobstate_file}'
        exit 0
    elif test ${{status}} -eq 77; then
        echo SUCCESS/SKIPPED > '{jobstate_file}'
        exit 0
    else
        echo FAILED > '{jobstate_file}'
        exit ${{status}}
    fi
}}
trap 'trap_state $?' EXIT
"""

                    # Parse command line
                    f += """
JOBID="$1"
RETURN="$2"
test "X${JOBID}" != X || fail 'missing JOBID argument to post script'
test "X${RETURN}" != X || fail 'missing RETURN argument to post script'
"""

                    # Write job batch ID file
                    f += f"""
echo "${{JOBID}}" > '{jobbatchID_file}'
"""

                    # Skip post script if job was skipped
                    f += """
if test ${RETURN} -eq 77; then
    exit ${RETURN}
fi
"""

                    # Check for output files, and link to final output directory
                    for outfile in job["outfiles"]:
                        f += f"test -f '{outfile}' || fail 'Missing output file {outfile.name}'"
                        if job["outfinal"]:
                            outfinaldir_file = outfinaldir / outfile.name
                            f += f"test ! -f '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                            f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                    # Exit with job return code
                    f += "exit ${RETURN}"

                ### Build HTCondor job submit file ###

                sub: Dict[str, Optional[str]] = {}

                # - Job universe
                sub["UNIVERSE"] = job["batchopt"].get("universe", self.universe)

                # - Job log
                sub["LOG"] = job_log

                # - Job directory should be on execution point
                sub["INITIALDIR"] = None

                # - Job script
                sub["EXECUTABLE"] = "/bin/bash"
                sub["ARGUMENTS"] = "./job_script.sh"
                sub["TRANSFER_EXECUTABLE"] = "FALSE"
                sub["GETENV"] = "FALSE"

                # - Job does not read standard input
                sub["INPUT"] = None

                # - Job standard output/error
                sub["OUTPUT"] = "job_stdout"
                sub["ERROR"] = "job_stderr"

                # - Job input and output files
                #   - HTCondor automatically identifies and transfers output files
                sub["SHOULD_TRANSFER_FILES"] = "YES"
                sub["TRANSFER_INPUT_FILES"] = ",".join(
                    str(infile) for infile in job["infiles"]
                )
                sub["WHEN_TO_TRANSFER_OUTPUT"] = "ON_EXIT"

                # - Job resources
                sub["REQUEST_MEMORY"] = "{}M".format(job["mem_MB"])
                sub["REQUEST_DISK"] = "{}M".format(job["disk_MB"])
                sub["REQUEST_CPUS"] = job["ncpus"]
                if job["ngpus"] > 0:
                    sub["REQUEST_GPUS"] = job["ngpus"]

                # - Extra HTCondor job submit file options
                submit_opt = self.default_submit_opt.copy()
                if "sub" in job["batchopt"]:
                    for opt, val in job["batchopt"]["sub"].items():
                        submit_opt[opt.upper()] = val
                for opt, val in submit_opt.items():
                    if opt in sub:
                        msg = f"job submit file option '{opt}' is already defined"
                        raise WorkflowException(msg)
                    sub[opt] = val

                # Write HTCondor job submit file
                job_sub_file = job["dir"] / "job_condor.sub"
                self._write_sub(account, job_sub_file, sub)

                ### Add job to HTCondor DAG input file ###

                # - Job ID within DAG
                job_DAG_ID = "job_" + job["ID"]

                # - Define job
                F += f"JOB {job_DAG_ID} {job_sub_file} DIR {job['dir']}"

                # - Define pre- and post-job script
                F += f"""
SCRIPT PRE {job_DAG_ID} /bin/bash {job_pre_file}
PRE_SKIP {job_DAG_ID} 77
SCRIPT POST {job_DAG_ID} /bin/bash {job_post_file} $JOBID $RETURN
"""

                # - Define prerequisite jobs
                if job["needs"]:
                    job_DAG_parent_IDs = " ".join(
                        "job_" + needs_job_ID for needs_job_ID in job["needs"]
                    )
                    F += f"PARENT {job_DAG_parent_IDs} CHILD {job_DAG_ID}"

            # Write workflow state job script
            workstate_script_file = batchdir / "batch_workstate.sh"
            with FileLineWriter(workstate_script_file) as f:

                # Exit on any script errors
                f += "set -e"

                # Ensure list of directories of failed jobs does not exist
                f += f"rm -f '{failedjobfile}'"

                # Default to success
                f += "workstate=SUCCESS"

                # Check DAG output for exit status
                dagman_out = batchdir / "batch_condor.dag.dagman.out"
                f += f"""
if tail -n 1 '{dagman_out}' | grep -q -v "EXITING WITH STATUS"; then
    echo "info: monitoring '{dagman_out}' for DAG exit status..."
    ( tail -f -n 1 '{dagman_out}' & ) | grep -q "EXITING WITH STATUS"
fi
if tail -n 1 '{dagman_out}' | grep -q -v "EXITING WITH STATUS 0"; then
    workstate=FAILED
fi
"""

                # Check state of all jobs
                # - write directories of failed jobs to file
                # - if a job is still RUNNING, it clearly didn't
                #   complete successfully, so mark as FAILED
                for job in jobs.values():
                    jobstate_file = job["dir"] / "job_state"
                    f += f"""
jobstate=`cat '{jobstate_file}' 2>/dev/null || true`
if test "X${{jobstate%%/*}}" != XSUCCESS; then
    echo '{job['dir']}' >> '{failedjobfile}'
    workstate=FAILED
    if test "X${{jobstate}}" = XRUNNING; then
        echo FAILED > '{jobstate_file}'
    fi
fi
"""

                # Write workflow state
                f += f"""echo "${{workstate}}" > '{workstatefile}'"""

                # Remove file indicating workflow has been submitted
                f += f"rm -f '{submittedfile}'"

            # Write workflow state HTCondor job submit file
            # - This is decoupled from the DAG so it can monitor its output
            workstate_sub_file = batchdir / "batch_workstate_condor.sub"
            self._write_sub(
                account,
                workstate_sub_file,
                {
                    "batch_name": f"{workflowname}_{ID}_state",
                    "universe": "local",
                    "log": batchdir / "batch_workstate_condor.log",
                    "executable": "/bin/bash",
                    "arguments": f'"{workstate_script_file} $(DAG_STATUS)"',
                    "output": batchdir / "batch_workstate_stdout",
                    "error": batchdir / "batch_workstate_stderr",
                },
            )

            # Write submit file for final HTCondor DAG
            final_sub_file = batchdir / "batch_final_condor.sub"
            self._write_sub(
                account,
                final_sub_file,
                {
                    "universe": "local",
                    "log": job_log,
                    "executable": "/bin/bash",
                    "arguments": f'''"-c 'condor_submit {workstate_sub_file}'"''',
                    "output": batchdir / "batch_final_stdout",
                    "error": batchdir / "batch_final_stderr",
                },
            )

            # Write job to HTCondor DAG input file
            F += f"FINAL final {final_sub_file}"

    def get_ctrl_commands(self, *, workflowname, basebatchdir) -> Tuple[str, str, str]:
        """Get top-level control script commands."""

        # Shell function to submit workflows in `basebatchdir` with IDs in $@
        # - If HTCondor DAG has already been run successfully, recreate workflow state
        submit = f"""
submit() {{
    local ID=
    for ID in $@; do
        cd "{basebatchdir}/${{ID}}"
        local dagman_out="./batch_condor.dag.dagman.out"
        local rescue_dags=`shopt -s nullglob; echo ./batch_condor.dag.rescue*`
        if test -f "${{dagman_out}}" && test "X${{rescue_dags}}" = X; then
            /bin/bash ./batch_workstate.sh
        else
            condor_submit_dag -AlwaysRunPost -batch-name "{workflowname}_${{ID}}" ./batch_condor.dag >/dev/null
        fi
    done
}}
"""

        # Shell function to display all queued jobs from workflows in `basebatchdir` with IDs in $@
        queue = f"""
queue() {{
    local constraint=
    local dagman_outs=
    local ID=
    for ID in $@; do
        if test "X${{constraint}}" != X; then
            constraint="${{constraint}} || "
        fi
        constraint="${{constraint}}JobBatchName == \\\"{workflowname}_${{ID}}\\\""
        local dagman_out="{basebatchdir}/${{ID}}/batch_condor.dag.dagman.out"
        if test -f "${{dagman_out}}"; then
            dagman_outs="${{dagman_outs}} ${{dagman_out}}"
        fi
    done
    constraint="( DAGManJobId != 0 ) && ( ${{constraint}} )"
    condor_q -constraint "${{constraint}}"
    if test "X${{dagman_outs}}" != X; then
        local awk_script='{{ print }}'
        if test -t; then
            local cols=`tput cols 2>/dev/null || echo 96`
            awk_script="BEGIN {{ C = ${{cols}} - 24 }} "'{{ if (length($0) > C) {{ print substr($0, 0, C), "..." }} else {{ print }} }}'
        fi
        tail -v -n 5 ${{dagman_outs}} | awk "${{awk_script}}"
    fi
}}
"""

        # Shell function to cancel all jobs from from workflows in `basebatchdir` with IDs in $@
        cancel = f"""
cancel() {{
    local constraint=
    local ID=
    for ID in $@; do
        local dagman_out="{basebatchdir}/${{ID}}/batch_condor.dag.dagman.out"
        if grep -q -v "Event: ULOG_SUBMIT" "${{dagman_out}}"; then
            echo "info: monitoring '${{dagman_out}}' for job submit events..."
            ( tail -f -n 10 "${{dagman_out}}" & ) | grep -q "Event: ULOG_SUBMIT"
        fi
        if test "X${{constraint}}" != X; then
            constraint="${{constraint}} || "
        fi
        constraint="${{constraint}}JobBatchName == \\\"{workflowname}_${{ID}}\\\""
    done
    constraint="( DAGManJobId != 0 ) && ( ${{constraint}} )"
    condor_rm -constraint "${{constraint}}"
}}
"""

        return submit, queue, cancel
