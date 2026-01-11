# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Generic batch workflows.

Generic workflow writer for PBS/Slurm/etc. style batch systems.
"""

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

from ._util import (
    SCRIPT_HEADER,
    FileLineWriter,
    ProgressLogger,
    WorkflowWriter,
    job_dir_from_ID,
    request_disk_for_external_input_files,
)


class BatchQWorkflowWriter(WorkflowWriter, ABC):
    """Write generic batch workflow."""

    # Configuration keys for generic workflow
    KEYS: Dict[str, Any] = {
        "jobfs": None,
        "link_input_files?": None,
        "n_arraymax": 0,
        "n_jobsubpermin": 300,
    }

    # Job script header; should define the fields:
    # - {jobname}: name of job
    # - {account}: account to which job is charged
    # - {time_min}: requested wall time in minutes
    # - {mem_MB}: requested memory in MB
    # - {disk_MB}: requested job scratch space in MB
    # - {ncpus}: requested number of CPUs
    # - {stdout}: redirect job standard output to this file
    # - {stderr}: redirect job standard error to this file
    BATCHQ_HEADER: str

    # Job script header for requesting {n} GPUs
    BATCHQ_HEADER_NGPUS: str

    # Job script header for an arbitrary comment
    BATCHQ_HEADER_COMMENT: str

    # Name of environment variable which gives
    # the batch system ID of the current job
    BATCHQ_JOB_ID: str

    # Expression which gives the batch system ID of the
    # current job in a job array
    BATCHQ_JOB_ARRAY_ID_EXPR: str

    # Name of environment variable which gives the batch array index
    # - This may not be the same as the **job array** index: it is
    #   assumed that the batch system can only handle contiguous
    #   job arrays, and so a mapping is created between indexes in
    #   the batch system, and indexes used by BatchQWorkflowWriter.
    #   For example, if only jobs 1, 3, 7 need to be run:
    #       job array indexes: 1, 3, 7 in BatchQWorkflowWriter
    #     batch array indexes: 0, 1, 2 in batch system
    BATCHQ_ARRAY_INDEX: str

    # Syntax for creating a job array with a single job (with batch index 0)
    BATCHQ_ARRAY_SINGLETON: str

    # Syntax for creating a job array with batch indexes from 0 to {n}
    BATCHQ_ARRAY_RANGE: str

    # String for encoding a dependency on a job in a job array.
    # May define the format() fields:
    # - batch_ID: batch system ID of job array
    # - batch_index: batch index of job in job array to depend on.
    #   Omit this field if batch system does not support depending
    #   on a job within a job array; instead the whole job array must
    #   be a dependency
    BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX: str

    # Argument used to submit job dependencies
    BATCHQ_SUBMIT_ARG_DEPEND: str

    # Argument used to submit job array batch indexes
    BATCHQ_SUBMIT_ARG_ARRAY: str

    # Pattern that will be replaced by job array batch index in submit files
    BATCHQ_SUBMIT_ARRAY_INDEX_REPL: str

    # Command to submit a job array, and the type
    # of dependency to use for prerequisite array jobs.
    BATCHQ_SUBMIT_ARRAY: str
    BATCHQ_SUBMIT_ARRAY_DEPEND: str

    # Command to submit the final state job and the type
    # of dependency to use for prerequisite jobs.
    # - The final state job should always run even if the
    #   workflow jobs (its prerequisites) have failed, so
    #   BATCHQ_SUBMIT_STATE_DEPEND should be e.g. `afterany`.
    BATCHQ_SUBMIT_STATE: str
    BATCHQ_SUBMIT_STATE_DEPEND: str

    # Bash parameter expansion command to remove a suffix from the
    # output of a submit command to get a batch system ID
    # - For example, "%%.*" removes all output from the first period
    BATCHQ_ID_REMOVE_SUFFIX: str

    # Location of job scratch directory
    jobfs: str

    # Maximum size of job array
    max_array_size: int

    @abstractmethod
    def batchq_max_array_size(self) -> int:
        """Get maximum size of a job array."""

    @abstractmethod
    def batchq_ctrl_commands(self) -> Tuple[str, str, str]:
        """Get top-level batch control script commands."""

    def __init__(self, *, batchsyscfg):
        super().__init__(batchsyscfg=batchsyscfg)

        # Store location of job scratch directory
        self.jobfs = batchsyscfg["jobfs"]

        # Store whether to link or copy input files
        self.link_input_files = batchsyscfg["link_input_files?"]

        # Store maximum size of a job array
        if batchsyscfg["n_arraymax"] > 0:
            self.max_array_size = batchsyscfg["n_arraymax"]
        else:
            self.max_array_size = self.batchq_max_array_size()

        # Store rate jobs can be submitted per minute
        self.job_submit_per_min = float(batchsyscfg["n_jobsubpermin"])

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

        if not self.link_input_files:

            # Request temporary disk space for external input files
            request_disk_for_external_input_files(jobs.values())

        # Guestimate runtime for submission and workstate jobs
        # - At least 10 minutes
        submit_time_min = max(10, math.ceil(len(jobs) / self.job_submit_per_min))

        # Bundle jobs by order, dependencies, and resources
        job_bundles: Dict[Tuple[int, str], Any] = {}
        for job in jobs.values():
            job_bundle_ID = "_".join(
                (
                    "{needs_ID}",
                    "time-{time_min}-min",
                    "mem-{mem_MB}-MB",
                    "disk-{disk_MB}-MB",
                    "ncpus-{ncpus}",
                    "ngpus-{ngpus}",
                )
            ).format(**job)
            job_bundle_key = (job["order"], job_bundle_ID)
            if job_bundle_key not in job_bundles:
                job_bundles[job_bundle_key] = []
            job_bundles[job_bundle_key].append(job)

        # Split job groups into array of maximum size
        job_arrays = {}
        for job_bundle_key in job_bundles:
            job_bundle_order, job_bundle_ID = job_bundle_key
            job_bundle = job_bundles[job_bundle_key]

            # Try to create evenly sized job arrays
            array_size = math.floor(
                len(job_bundle) / math.ceil(len(job_bundle) / self.max_array_size)
            )
            assert array_size <= self.max_array_size

            # Create jobs arrays
            for idx in range(0, len(job_bundle), self.max_array_size):
                job_array = job_bundle[idx : idx + self.max_array_size]
                job_array_ID = f"{job_bundle_ID}_idx-{idx}"
                job_array_key = (job_bundle_order, job_array_ID)
                job_arrays[job_array_key] = job_array

        # Write job arrays
        job_ID_to_job_array_ID_index = {}
        for (_, job_array_ID), job_array in ProgressLogger(
            sorted(job_arrays.items()),
            msg="wrote %s job arrays in workflow",
        ):

            # Make job array directory
            jobarraydir = job_dir_from_ID(batchdir / "job_arrays", job_array_ID)
            jobarraydir.mkdir(parents=True, exist_ok=True)

            # Use first job in array for retrieving resources
            job = job_array[0]

            # Write job array submission script
            jobarray_submit_script_file = jobarraydir / "job_array_submit.sh"
            jobarray_script_file = jobarraydir / "job_array_script.sh"
            with FileLineWriter(jobarray_submit_script_file) as f:
                f += "#!/bin/bash"

                # Write job script header
                f += self.BATCHQ_HEADER.format(
                    jobname=f"{workflowname}_{ID}",
                    account=account,
                    time_min=job["time_min"],
                    mem_MB=job["mem_MB"],
                    disk_MB=job["disk_MB"],
                    ncpus=job["ncpus"],
                    stdout=jobarraydir
                    / f"job_stdout_{job_array_ID}_{self.BATCHQ_SUBMIT_ARRAY_INDEX_REPL}",
                    stderr=jobarraydir
                    / f"job_stderr_{job_array_ID}_{self.BATCHQ_SUBMIT_ARRAY_INDEX_REPL}",
                )
                if job["ngpus"] > 0:
                    f += self.BATCHQ_HEADER_NGPUS.format(n=job["ngpus"])
                if "comment" in job["batchopt"]:
                    f += self.BATCHQ_HEADER_COMMENT.format(
                        comment=job["batchopt"]["comment"]
                    )

                # Exit on any script errors
                f += "set -e"

                # Run job array script
                f += f"source '{jobarray_script_file}'"

            with FileLineWriter(jobarray_script_file) as f:

                # Write common script header
                f += SCRIPT_HEADER

                # Write shell function stage_infile() to stage input files
                stage_infile_cmd = "ln -s" if self.link_input_files else "cp"
                f += f"""
                function stage_infile() {{
                    local dir="$1"
                    local name="$2"
                    test -f "${{dir}}/${{name}}" || fail "Missing input file ${{dir}}/${{name}}"
                    {stage_infile_cmd} "${{dir}}/${{name}}" "./${{name}}"
                }}
                """

                # Parse job array indexes to find which job to run based on batch index
                jobarray_index_file = jobarraydir / "job_array_indexes"
                batch_index = f"${{{self.BATCHQ_ARRAY_INDEX}}}"
                f += f"""
                set -- `cat '{jobarray_index_file}'`
                batchq_array_count="$1"
                shift
                if test {batch_index} -lt 0 || test {batch_index} -ge ${{batchq_array_count}}; then
                    fail "{self.BATCHQ_ARRAY_INDEX}={batch_index} must be in the range 0--$((batchq_array_count-1))"
                fi
                batchq_array_index=0
                while test ${{batchq_array_index}} -lt {batch_index}; do
                    shift
                    : $((++batchq_array_index))
                done
                batchq_job_index="$1"
                """

                # Change to job scratch directory
                f += f"""test -d "{self.jobfs}" || mkdir -p "{self.jobfs}" || fail 'Could not make scratch directory {self.jobfs}'"""
                f += f'cd "{self.jobfs}"'

                # Select which job in job array to run
                f += "case ${batchq_job_index} in"
                with f.indent(4):

                    # Iterate over jobs in job array
                    for idx, job in enumerate(job_array):
                        f += f"({idx})"
                        with f.indent(4):

                            # Map individual job IDs to job array IDs and indexes
                            job_ID_to_job_array_ID_index[job["ID"]] = (
                                job_array_ID,
                                idx,
                            )

                            # Print comment used to extract job indexes and directories
                            f += f"# JOB {idx} {job['dir']}"

                            # Write job batch ID to file
                            jobbatchID_file = job["dir"] / "job_batchID"
                            f += f"""echo "{self.BATCHQ_JOB_ARRAY_ID_EXPR}" > '{jobbatchID_file}'"""

                            # Set trap to write job state file
                            # - for signals, exit with 128+<signal number>
                            jobstate_file = job["dir"] / "job_state"
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
                            trap 'trap_state 143' TERM
                            write_atomic RUNNING '{jobstate_file}'
                            """

                            # Check that all prerequisite jobs were successfully run
                            for needs_job_ID in job["needs"]:

                                # Path to file indicating job state
                                needs_jobstate_file = (
                                    jobs[needs_job_ID]["dir"] / "job_state"
                                )

                                # Test that job was successful, or skip if it was skipped
                                f += f"""
                                jobstate=`cat '{needs_jobstate_file}' 2>/dev/null || true`
                                if test "X${{jobstate}}" = XSUCCESS/SKIPPED; then
                                    exit {skip_exit_code}
                                elif test "X${{jobstate}}" != XSUCCESS; then
                                    fail 'Prerequisite job {needs_job_ID} was not successful'
                                fi
                                """

                            # Hard-link standard output/error files from job array back to job directory
                            for stdouterr in ("job_stdout", "job_stderr"):
                                jobarray_stdouterr_file = (
                                    jobarraydir
                                    / f"{stdouterr}_{job_array_ID}_{batch_index}"
                                )
                                job_stdouterr_file = job["dir"] / stdouterr
                                f += f'ln -f "{jobarray_stdouterr_file}" "{job_stdouterr_file}"'

                            # Stage input files into job scratch directory
                            for infile in job["infiles"] + job["extinfiles"]:
                                f += f"stage_infile '{infile.parent}' '{infile.name}'"

                            # Run job script
                            f += "source ./job_script.sh"

                            # Copy output files out of job scratch directory
                            for outfile in job["outfiles"] + job["joboutfiles"]:
                                f += f"test -f './{outfile.name}' || fail 'Missing output file {outfile.name}'"
                                f += f"cp './{outfile.name}' '{outfile}'"
                            if job["outfinal"]:
                                f += f"mkdir -p '{outfinaldir}'"
                                for outfile in job["outfiles"]:
                                    outfinaldir_file = outfinaldir / outfile.name
                                    f += f"test ! -f '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                                    f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                            f += ";;"

                    # Handle an incorrect job array index
                    f += "(*)"
                    with f.indent(4):
                        f += (
                            '''fail "Incorrect job array index '${batchq_job_index}'"'''
                        )

                # end: Select which job in job array to run
                f += "esac"

        # Write submission script to check all jobs have succeeded
        workstate_submit_script_file = batchdir / "workstate_submit.sh"
        workstate_script_file = batchdir / "workstate_script.sh"
        with FileLineWriter(workstate_submit_script_file) as f:
            f += "#!/bin/bash"

            # Write job script header
            f += self.BATCHQ_HEADER.format(
                jobname=f"{workflowname}_{ID}_state",
                account=account,
                time_min=submit_time_min,
                mem_MB=10,
                disk_MB=10,
                ncpus=1,
                stdout=batchdir / "workstate_stdout",
                stderr=batchdir / "workstate_stderr",
            )

            # Exit on any script errors
            f += "set -e"

            # Run script to check all jobs have succeeded
            f += f"source '{workstate_script_file}'"

        with FileLineWriter(workstate_script_file) as f:

            # Write common script header
            f += SCRIPT_HEADER

            # Ensure list of directories of failed jobs does not exist
            f += f"rm -f '{failedjobfile}'"

            # Default to success
            f += "workstate=SUCCESS"

            # Check state of all jobs
            # - write directories of failed jobs to file
            # - if a job is still RUNNING, it clearly didn't
            #   complete successfully, so mark as FAILED
            for _, job_array in job_arrays.items():
                for job in job_array:
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

        # Register which jobs have been written
        all_jobs_ID_written = set()

        # Write submission script to submit all jobs
        all_jobs_submit_script_file = batchdir / "all_submit.sh"
        all_jobs_script_file = batchdir / "all_script.sh"
        with FileLineWriter(all_jobs_submit_script_file) as f:
            f += "#!/bin/bash"

            # Write job script header
            f += self.BATCHQ_HEADER.format(
                jobname=f"{workflowname}_{ID}",
                account=account,
                time_min=submit_time_min,
                mem_MB=10,
                disk_MB=10,
                ncpus=1,
                stdout=batchdir / "all_stdout",
                stderr=batchdir / "all_stderr",
            )

            # Exit on any script errors
            f += "set -e"

            # Run script to submit all jobs
            f += f"source '{all_jobs_script_file}'"

        with FileLineWriter(all_jobs_script_file) as f:

            # Write common script header
            f += SCRIPT_HEADER

            # Set trap to write the workflow state if errors are encountered
            # - for signals, exit with 128+<signal number>
            f += f"""
            function trap_state() {{
                local status=$1
                if test ${{status}} -ne 0; then
                    write_atomic FAILED '{workstatefile}'
                    rm -f '{submittedfile}'
                fi
                exit ${{status}}
            }}
            trap 'trap_state $?' EXIT
            trap 'trap_state 143' TERM
            """

            # Write shell function job_array_indexes() which
            # determines which indexes of an array job need to be run
            f += """
            function job_array_indexes() {
                local idx=
                local jobdir=
                batchq_array_indexes=
                batchq_array_count=0
                while read idx jobdir; do
                    jobstate=`cat "${jobdir}/job_state" 2>/dev/null || true`
                    if test "X${jobstate%%/*}" != XSUCCESS; then
                        batchq_array_indexes="${batchq_array_indexes} ${idx}"
                        : $((++batchq_array_count))
                    fi
                done < <(sed -n 's/^ *# JOB //p' "$1")
            }
            """

            # Write shell function job_array_needs_ID_idx() which
            # parses job array indexes to determine the batch index of
            # a dependent job, and returns the dependent batch ID/index
            needs_ID_idx_format = self.BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX.format(
                batch_ID="${batchID}",
                batch_index="${batchidx}",
            )
            f += f"""
            function job_array_needs_ID_idx() {{
                local batchID="$1"
                local jobidxs="$2"
                local jobidx="$3"
                local batchidx=0
                set -- `cat "${{jobidxs}}"`
                shift
                while test "$1" -ne ${{jobidx}}; do
                    shift
                    test "X$1" != X || fail "job_array_needs_index() has run out of job array indexes"
                    : $((++batchidx))
                done
                batchq_needs_ID_idx="{needs_ID_idx_format}"
            }}
            """

            # Save all batch system IDs for state job
            f += "batchq_all_IDs="

            # Write commands to submit each job array
            for (_, job_array_ID), job_array in sorted(job_arrays.items()):

                # Job array directory
                jobarraydir = job_dir_from_ID(batchdir / "job_arrays", job_array_ID)

                # Use first job in array for retrieving dependencies
                job = job_array[0]

                # Get batch system IDs of prerequisite jobs
                f += "batchq_needs_IDs="
                for needs_job_ID in job["needs"]:

                    # Check that prerequisite job has been written
                    assert needs_job_ID in all_jobs_ID_written

                    # Path to file indicating job state
                    needs_jobstate_file = jobs[needs_job_ID]["dir"] / "job_state"

                    # Map individual needed job ID to needed job array ID and index
                    needs_job_array_ID, needs_job_array_idx = (
                        job_ID_to_job_array_ID_index[needs_job_ID]
                    )

                    # Needed job array directory
                    needs_jobarraydir = job_dir_from_ID(
                        batchdir / "job_arrays", needs_job_array_ID
                    )

                    # Add batch system ID of prerequisite job to dependency
                    needs_jobarray_batchqID_file = (
                        needs_jobarraydir / "job_array_batchqID"
                    )
                    needs_jobarray_index_file = needs_jobarraydir / "job_array_indexes"
                    f += f"""
                    jobstate=`cat '{needs_jobstate_file}' 2>/dev/null || true`
                    if test "X${{jobstate}}" != XSUCCESS && test "X${{jobstate}}" != XSUCCESS/SKIPPED; then
                        test -f '{needs_jobarray_batchqID_file}' || fail 'Prerequisite job array {needs_job_array_ID} has not been submitted'
                        batchq_ID=`cat '{needs_jobarray_batchqID_file}'`
                        job_array_needs_ID_idx "${{batchq_ID}}" '{needs_jobarray_index_file}' '{needs_job_array_idx}'
                        batchq_needs_IDs="${{batchq_needs_IDs}}:${{batchq_needs_ID_idx}}"
                    fi
                    """

                # Determine job array indexes which should be submitted
                jobarray_submit_script_file = jobarraydir / "job_array_submit.sh"
                jobarray_script_file = jobarraydir / "job_array_script.sh"
                jobarray_index_file = jobarraydir / "job_array_indexes"
                batch_array_range = self.BATCHQ_ARRAY_RANGE.format(
                    n="$((batchq_array_count-1))"
                )
                f += f"""
                job_array_indexes '{jobarray_script_file}'
                echo ${{batchq_array_count}} ${{batchq_array_indexes}} > '{jobarray_index_file}'
                if test ${{batchq_array_count}} -eq 1; then
                    batchq_array='{self.BATCHQ_ARRAY_SINGLETON}'
                else
                    batchq_array="{batch_array_range}"
                fi
                """

                # If jobs in array need to be run, submit them and save job ID
                # - jobs are initially submitted in a held state
                jobarray_batchqID_file = jobarraydir / "job_array_batchqID"
                f += f"""
                if test ${{batchq_array_count}} -eq 0; then
                    batchq_ID=DONE
                else
                    batchq_dependency="{self.BATCHQ_SUBMIT_ARG_DEPEND}afterok:${{{self.BATCHQ_JOB_ID}{self.BATCHQ_ID_REMOVE_SUFFIX}}}"
                    if test "X${{batchq_needs_IDs}}" != X; then
                        batchq_dependency="${{batchq_dependency}},{self.BATCHQ_SUBMIT_ARRAY_DEPEND}${{batchq_needs_IDs}}"
                    fi
                    rm -f '{jobarraydir}/"job_stdout_'* '{jobarraydir}/"job_stderr_'*
                    batchq_ID=`{self.BATCHQ_SUBMIT_ARRAY} ${{batchq_dependency}} {self.BATCHQ_SUBMIT_ARG_ARRAY}${{batchq_array}} '{jobarray_submit_script_file}'`
                    batchq_ID=`echo "${{batchq_ID{self.BATCHQ_ID_REMOVE_SUFFIX}}}"`
                    echo "info: submitted ${{batchq_array_count}} jobs in job array {job_array_ID}, batch ID ${{batchq_ID}}"
                    batchq_all_IDs="${{batchq_all_IDs}}:${{batchq_ID}}"
                fi
                echo "${{batchq_ID}}" > '{jobarray_batchqID_file}'
                """

                # Mark all jobs in array as having been written
                for job in job_array:
                    all_jobs_ID_written.add(job["ID"])

            # Submit final state job
            f += f"""
            batchq_dependency="{self.BATCHQ_SUBMIT_ARG_DEPEND}afterok:${{{self.BATCHQ_JOB_ID}{self.BATCHQ_ID_REMOVE_SUFFIX}}}"
            if test "X${{batchq_all_IDs}}" != X; then
                batchq_dependency="${{batchq_dependency}},{self.BATCHQ_SUBMIT_STATE_DEPEND}${{batchq_all_IDs}}"
            fi
            batchq_ID=`{self.BATCHQ_SUBMIT_STATE} ${{batchq_dependency}} '{workstate_submit_script_file}'`
            batchq_ID=`echo "${{batchq_ID{self.BATCHQ_ID_REMOVE_SUFFIX}}}"`
            echo "info: submitted state job, batch ID ${{batchq_ID}}"
            """

    def get_ctrl_commands(self, *, workflowname) -> Tuple[str, str, str]:
        """Get top-level control script commands."""

        # Get batch system commands
        submit, queue, cancel = self.batchq_ctrl_commands()

        # Shell function to submit workflows in `baseworkflowdir` with IDs in $@
        submit += """
        submit() {
            local scripts=
            local ID=
            for ID in $@; do
                scripts="${scripts} ./workflows/${ID}/batch_scripts/all_submit.sh"
            done
            batchq_submit ${scripts}
        }
        """

        # Shell function to display all queued jobs from workflows in `baseworkflowdir` with IDs in $@
        queue += f"""
        queue() {{
            local jobnames=
            local ID=
            for ID in $@; do
                all_stderr="./workflows/${{ID}}/batch_scripts/all_stderr"
                if test -s "${{all_stderr}}"; then
                    sed "s/^/ERROR: ${{ID}}: /" "${{all_stderr}}"
                fi
                jobnames="${{jobnames}} {workflowname}_${{ID}}"
            done
            batchq_queue ${{jobnames}}
        }}
        """

        # Shell function to cancel all jobs from from workflows in `baseworkflowdir` with IDs in $@
        cancel += f"""
        cancel() {{
            local jobnames=
            local ID=
            for ID in $@; do
                jobnames="${{jobnames}} {workflowname}_${{ID}}"
            done
            batchq_cancel ${{jobnames}}
        }}
        """

        return submit, queue, cancel
