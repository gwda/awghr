# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""HTCondor workflows."""

import shlex
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ._util import (
    SCRIPT_HEADER,
    FileLineWriter,
    ProgressLogger,
    WorkflowException,
    WorkflowWriter,
    job_dir_from_ID,
    request_disk_for_external_input_files,
)


class HTCondorWorkflowWriter(WorkflowWriter):
    """Write HTCondor workflow."""

    # HTCondor-specific configuration keys
    KEYS: Dict[str, Any] = {
        "universe": "vanilla",
        "submit_cmd_account": "accounting_group",
        "submit_cmd_account_user": "accounting_group_user",
        "submit_opt_list": [],
        "file_transfer_scheme_list": [],
        "periodic_release_hold_codes": "4 7 8 9 10 11 12 13 14 17 18 19 22 23 24",
    }

    def __init__(self, *, batchsyscfg):
        super().__init__(batchsyscfg=batchsyscfg)

        # Store default universe
        self.universe = batchsyscfg["universe"]

        # Store name of command used to add account and associated user to HTCondor submit file
        self.account_cmd = batchsyscfg["submit_cmd_account"]
        self.account_user_cmd = batchsyscfg["submit_cmd_account_user"]

        # Store default HTCondor job submit file options
        self.default_submit_opt = {}
        for opt_str in batchsyscfg["submit_opt_list"]:
            opt, val = (s.strip() for s in opt_str.split("=", maxsplit=1))
            self.default_submit_opt[opt.upper()] = val

        # Store file tranfer scheme map
        self.file_transfer_scheme = []
        for scheme_str in batchsyscfg["file_transfer_scheme_list"]:
            scheme_root, scheme = (s.strip() for s in scheme_str.split("=", maxsplit=1))
            self.file_transfer_scheme.append((Path(scheme_root), scheme))

        # Store list of hold reason codes where job can be periodically released
        periodic_release_hold_codes_list = [
            s.strip() for s in batchsyscfg["periodic_release_hold_codes"].split()
        ]
        self.periodic_release_hold_codes = (
            "{" + ", ".join(periodic_release_hold_codes_list) + "}"
        )

    def _write_sub(self, account, sub_file, sub):
        """Write HTCondor submit file."""
        sub = sub.copy()

        # Add accounting entries
        if "@" in account:
            account_user, account = account.split("@", maxsplit=1)
            sub[self.account_user_cmd.upper()] = account_user
        sub[self.account_cmd.upper()] = account

        # Write submit file
        with FileLineWriter(sub_file) as f:
            for sub_opt, sub_val in sub.items():
                if sub_val is not None:
                    f += f"{sub_opt} = {sub_val}"
            f += "queue"

    def _apply_file_transfer_scheme(self, path):
        """Apply file transfer scheme map."""
        for scheme_root, scheme in self.file_transfer_scheme:
            try:
                scheme_path = path.relative_to(scheme_root)
            except ValueError:
                continue
            return f"{scheme}:///{scheme_path}"
        return str(path)

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

        # Request temporary disk space for external input files
        request_disk_for_external_input_files(jobs.values())

        # HTCondor job log
        job_log = batchdir / "condor.log"

        # Write HTCondor DAG input file
        dag_file = batchdir / "condor.dag"
        with FileLineWriter(dag_file) as F:

            # Write jobs
            for job in ProgressLogger(jobs.values(), msg="wrote %s jobs in workflow"):

                # Job ID string for use in HTCondor submission files
                job_ID = "job_" + job["ID"]

                # Job batch ID file
                jobbatchID_file = job["dir"] / "job_batchID"

                # Job state file
                jobstate_file = job["dir"] / "job_state"

                # Job submission scripts directory
                jobscriptdir = job_dir_from_ID(batchdir / "job_scripts", job["ID"])
                jobscriptdir.mkdir(parents=True, exist_ok=True)

                # All input and output files for HTCondor to transfer
                job_all_infiles = job["infiles"] + job["extinfiles"]
                job_all_outfiles = job["outfiles"] + job["joboutfiles"]

                # Write job pre script
                job_pre_file = jobscriptdir / "job_condor_pre.sh"
                with FileLineWriter(job_pre_file) as f:

                    # Write common script header
                    f += SCRIPT_HEADER

                    # Capture standard output and error
                    job_pre_stdout_file = job["dir"] / "job_stdout_pre"
                    job_pre_stderr_file = job["dir"] / "job_stderr_pre"
                    f += f"exec >{job_pre_stdout_file} 2>{job_pre_stderr_file}"

                    # Check that all prerequisite jobs were not skipped
                    for needs_job_ID in job["needs"]:

                        # Path to file indicating job state
                        needs_jobstate_file = jobs[needs_job_ID]["dir"] / "job_state"

                        # Skip job if prerequisite job was skipped
                        f += f"""
                        jobstate=`cat '{needs_jobstate_file}' 2>/dev/null || true`
                        if test "X${{jobstate}}" = XSUCCESS/SKIPPED; then
                            write_atomic SUCCESS/SKIPPED '{jobstate_file}'
                            exit {skip_exit_code}
                        fi
                        """

                    # Write job batch ID file
                    # - HTCondor job ID is not available until job post script runs
                    # - Instead, write command to dynamically retrieve job ID
                    job_batchID_cmd = shlex.quote(
                        f"""!condor_q -constraint 'MY.AWGHR_JOB_ID == "{job_ID}"' -format '%d' ClusterId -format ".%d" ProcId"""
                    )
                    f += f"""echo {job_batchID_cmd} > '{jobbatchID_file}'"""

                    # Write job state file
                    f += f"write_atomic RUNNING '{jobstate_file}'"

                    # Remove standard output and error from any previous job
                    job_stdout_file = job["dir"] / "job_stdout"
                    job_stderr_file = job["dir"] / "job_stderr"
                    f += f"rm -f '{job_stdout_file}' '{job_stderr_file}'"

                # Write job wrapper script
                job_wrapper_file = jobscriptdir / "job_condor_wrapper.sh"
                job_missing_file = jobscriptdir / "job_condor_missing"
                with FileLineWriter(job_wrapper_file) as f:

                    # Write common script header
                    f += SCRIPT_HEADER

                    # Set trap to ensure that all output file exist on exit, as
                    # required by HTCondor's output file transfer mechanism, and
                    # record missing output files for later processing
                    # - for signals, exit with 128+<signal number>
                    f += "function trap_outfiles() {"
                    with f.indent(4):
                        f += """
                        local status=$1
                        touch ./job_condor_missing
                        """
                        for outfile in job_all_outfiles:
                            f += f"""
                            if ! test -f './{outfile.name}'; then
                                echo '{outfile}' >> ./job_condor_missing
                                touch './{outfile.name}'
                            fi
                            """
                        f += "exit ${status}"
                    f += """
                    }
                    trap 'trap_outfiles $?' EXIT
                    trap 'trap_outfiles 148' TSTP
                    """

                    # Run job script
                    f += "source ./job_script.sh"

                job_all_infiles.append(job_wrapper_file)
                job_all_outfiles.append(job_missing_file)

                # Write job post script
                job_post_file = jobscriptdir / "job_condor_post.sh"
                with FileLineWriter(job_post_file) as f:

                    # Write common script header
                    f += SCRIPT_HEADER

                    # Capture standard output and error
                    job_post_stdout_file = job["dir"] / "job_stdout_post"
                    job_post_stderr_file = job["dir"] / "job_stderr_post"
                    f += f"exec >{job_post_stdout_file} 2>{job_post_stderr_file}"

                    # Set trap to write job state file
                    # - for signals, exit with 128+<signal number>
                    f += f"""
                    function trap_state() {{
                        local status=$1
                        if test ${{status}} -eq 0; then
                            write_atomic SUCCESS '{jobstate_file}'
                            exit 0
                        elif test ${{status}} -eq {skip_exit_code}; then
                            write_atomic SUCCESS/SKIPPED '{jobstate_file}'
                            exit 0
                        else
                            write_atomic FAILED '{jobstate_file}'
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
                    # - HTCondor job ID is available to job post scripts
                    f += f"""
                    echo "${{JOBID}}" > '{jobbatchID_file}'
                    """

                    # Remove empty output files created by job wrapper script to
                    # ensure that all output file exist on exit, as required by
                    # HTCondor's output file transfer mechanism
                    f += f"""
                    missing_files=`cat '{job_missing_file}'`
                    if test "X${{missing_files}}" != X; then
                        rm -f ${{missing_files}}
                    fi
                    """

                    # Skip post script if job was skipped
                    f += f"""
                    if test ${{RETURN}} -eq {skip_exit_code}; then
                        exit ${{RETURN}}
                    fi
                    """

                    # Check for output files, and link to final output directory
                    for outfile in job_all_outfiles:
                        f += f"test -f '{outfile}' || fail 'Missing output file {outfile.name}'"
                    if job["outfinal"]:
                        f += f"mkdir -p '{outfinaldir}'"
                        for outfile in job["outfiles"]:
                            outfinaldir_file = outfinaldir / outfile.name
                            f += f"test ! -f '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                            f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                    # Exit with job return code
                    f += "exit ${RETURN}"

                ### Build HTCondor job submit file ###

                sub: Dict[str, Optional[str]] = {}

                # - Job ID
                sub["MY.AWGHR_JOB_ID"] = f'"{job_ID}"'

                # - Job universe
                sub["UNIVERSE"] = job["batchopt"].get("universe", self.universe)

                # - Job log
                sub["LOG"] = job_log

                # - Job directory should be on execution point
                sub["INITIALDIR"] = None

                # - Job script
                sub["EXECUTABLE"] = "/bin/bash"
                sub["ARGUMENTS"] = "./" + job_wrapper_file.name
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
                    self._apply_file_transfer_scheme(infile)
                    for infile in job_all_infiles
                )
                sub["TRANSFER_OUTPUT_FILES"] = ",".join(
                    outfile.name for outfile in job_all_outfiles
                )
                sub["TRANSFER_OUTPUT_REMAPS"] = '"{}"'.format(
                    ";".join(
                        "{}={}".format(
                            outfile.name, self._apply_file_transfer_scheme(outfile)
                        )
                        for outfile in job_all_outfiles
                    )
                )
                sub["WHEN_TO_TRANSFER_OUTPUT"] = "ON_EXIT"

                # - Job resources
                sub["REQUEST_MEMORY"] = "{}M".format(job["mem_MB"])
                sub["REQUEST_DISK"] = "{}M".format(job["disk_MB"])
                sub["REQUEST_CPUS"] = job["ncpus"]
                if job["ngpus"] > 0:
                    sub["REQUEST_GPUS"] = job["ngpus"]

                # - Periodically release held jobs
                sub["PERIODIC_RELEASE"] = "member(HoldReasonCode, {})".format(
                    self.periodic_release_hold_codes
                )

                # - Extra HTCondor job submit file options
                submit_opt = self.default_submit_opt.copy()
                for opt, val in job["batchopt"].items():
                    if opt[:4] == "sub_":
                        submit_opt[opt[4:].upper()] = val
                for opt, val in submit_opt.items():
                    if opt in sub:
                        msg = f"job submit file option '{opt}' is already defined"
                        raise WorkflowException(msg)
                    sub[opt] = val

                # Write HTCondor job submit file
                job_sub_file = jobscriptdir / "job_condor.sub"
                self._write_sub(account, job_sub_file, sub)

                ### Add job to HTCondor DAG input file ###

                # - Define job
                F += f"JOB {job_ID} {job_sub_file} DIR {job['dir']}"
                if "dag_retry" in job["batchopt"]:
                    dag_retry = int(job["batchopt"]["dag_retry"])
                    F += f"RETRY {job_ID} {dag_retry} UNLESS-EXIT {skip_exit_code}"

                # - Define pre- and post-job script
                F += f"""
                SCRIPT PRE {job_ID} /bin/bash {job_pre_file}
                PRE_SKIP {job_ID} {skip_exit_code}
                SCRIPT POST {job_ID} /bin/bash {job_post_file} $JOBID $RETURN
                """

                # - Define prerequisite jobs
                if job["needs"]:
                    job_parent_IDs = " ".join(
                        "job_" + needs_job_ID for needs_job_ID in job["needs"]
                    )
                    F += f"PARENT {job_parent_IDs} CHILD {job_ID}"

            # Write workflow state job script
            workstate_script_file = batchdir / "workstate.sh"
            with FileLineWriter(workstate_script_file) as f:

                # Write common script header
                f += SCRIPT_HEADER

                # Ensure list of directories of failed jobs does not exist
                f += f"rm -f '{failedjobfile}'"

                # Default to success
                f += "workstate=SUCCESS"

                # Check DAG output for exit status
                dagman_out = batchdir / "condor.dag.dagman.out"
                f += f"""
                if ! ( tail -n 1 '{dagman_out}' | grep -q "EXITING WITH STATUS" ); then
                    echo "info: monitoring '{dagman_out}' for DAG exit status..."
                    ( tail -f -n 1 '{dagman_out}' & ) | grep -q "EXITING WITH STATUS"
                fi
                if ! ( tail -n 1 '{dagman_out}' | grep -q "EXITING WITH STATUS 0" ); then
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
                            write_atomic FAILED '{jobstate_file}'
                        fi
                    fi
                    """

                # Write workflow state
                f += f"""write_atomic "${{workstate}}" '{workstatefile}'"""

                # Remove file indicating workflow has been submitted
                f += f"rm -f '{submittedfile}'"

            # Write workflow state HTCondor job submit file
            # - This is decoupled from the DAG so it can monitor its output
            workstate_sub_file = batchdir / "workstate_condor.sub"
            self._write_sub(
                account,
                workstate_sub_file,
                {
                    "batch_name": f"{workflowname}_{ID}_state",
                    "universe": "local",
                    "log": batchdir / "workstate_condor.log",
                    "executable": "/bin/bash",
                    "arguments": f'"{workstate_script_file} $(DAG_STATUS)"',
                    "output": batchdir / "workstate_stdout",
                    "error": batchdir / "workstate_stderr",
                },
            )

            # Write submit file for final HTCondor DAG
            final_sub_file = batchdir / "final_condor.sub"
            self._write_sub(
                account,
                final_sub_file,
                {
                    "universe": "local",
                    "log": job_log,
                    "executable": "/bin/bash",
                    "arguments": f'''"-c 'condor_submit {workstate_sub_file}'"''',
                    "output": batchdir / "final_stdout",
                    "error": batchdir / "final_stderr",
                },
            )

            # Write job to HTCondor DAG input file
            F += f"FINAL final {final_sub_file}"

    def get_ctrl_commands(self, *, workflowname) -> Tuple[str, str, str]:
        """Get top-level control script commands."""

        # Shell function to submit workflows in `baseworkflowdir` with IDs in $@
        # - If HTCondor DAG has already been run successfully, recreate workflow state
        submit = f"""
        submit() {{
            local ID=
            for ID in $@; do
                local dagman_out="./workflows/${{ID}}/batch_scripts/condor.dag.dagman.out"
                local rescue_dags=`shopt -s nullglob; echo ./workflows/${{ID}}/batch_scripts/condor.dag.rescue*`
                local workstate_script="./workflows/${{ID}}/batch_scripts/workstate.sh"
                local condor_dag="./workflows/${{ID}}/batch_scripts/condor.dag"
                if test -f "${{dagman_out}}" && test "X${{rescue_dags}}" = X; then
                    /bin/bash "${{workstate_script}}"
                else
                    condor_submit_dag -AlwaysRunPost -batch-name "{workflowname}_${{ID}}" "${{condor_dag}}" >/dev/null
                fi
            done
        }}
        """

        # Shell function to display all queued jobs from workflows in `baseworkflowdir` with IDs in $@
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
                local dagman_out="${{PWD}}/workflows/${{ID}}/batch_scripts/condor.dag.dagman.out"
                if test -f "${{dagman_out}}"; then
                    dagman_outs="${{dagman_outs}} ${{dagman_out}}"
                fi
            done
            constraint="( DAGManJobId != 0 ) && ( ${{constraint}} )"
            echo "##### ALL JOBS #####"
            condor_q -batch -constraint "${{constraint}}"
            echo "##### HELD JOBS #####"
            condor_q -hold -constraint "${{constraint}}"
            if test "X${{dagman_outs}}" != X; then
                echo "##### DAGMAN OUTPUT #####"
                echo
                local awk_script='{{ print }}'
                if test -t; then
                    local cols=`tput cols 2>/dev/null || echo 90`
                    awk_script="BEGIN {{ C = ${{cols}} - 30 }} "'{{ if (length($0) > C) {{ print substr($0, 0, C), "..." }} else {{ print }} }}'
                fi
                tail -v -n 10 ${{dagman_outs}} | awk "${{awk_script}}"
            fi
        }}
        """

        # Shell function to cancel all jobs from from workflows in `baseworkflowdir` with IDs in $@
        cancel = f"""
        cancel() {{
            local constraint=
            local ID=
            for ID in $@; do
                local dagman_out="./workflows/${{ID}}/batch_scripts/condor.dag.dagman.out"
                if ! ( grep -q "Event: ULOG_SUBMIT" "${{dagman_out}}" ); then
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
