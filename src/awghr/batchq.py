import logging
import math
from pathlib import Path

from .util import FileLineWriter, ProgressLogger, job_dir_from_ID

_logger = logging.getLogger(__name__)


# Generic workflow writer for PBS/Slurm/etc. style batch systems
class BatchQWorkflowWriter:

    # Job script header; should define the format() fields:
    # - jobname: name of job
    # - account: account to which job is charged
    # - time_min: requested wall time in minutes
    # - mem_MiB: requested memory in MiB
    # - disk_MiB: requested job scratch space in MiB
    # - threads: requested number of threads
    # - stdout: redirect job standard output to this file
    # - stderr: redirect job standard error to this file
    BATCHQ_HEADER: str

    # Name of environment variable which gives
    # the batch system ID of the current job
    BATCHQ_JOB_ID: str

    # Name of environment variable which gives
    # the job array index
    BATCHQ_ARRAY_INDEX: str

    # String for encoding a dependency on a job in a job array.
    # May define the format() fields:
    # - job_array_batchID: batch system ID of job array
    # - job_array_index: index of job in job array to depend on.
    #   Omit this field if batch system does not support depending
    #   on a job within a job array; instead the whole job array must
    #   be a dependency
    BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX: str

    # Argument used to submit job dependencies
    BATCHQ_SUBMIT_ARG_DEPEND: str

    # Argument used to submit job array indexes
    BATCHQ_SUBMIT_ARG_ARRAY: str

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

    # Command to submit job `script` that submits all jobs
    BATCHQ_SUBMIT_ALL: str

    # Command to display all queued jobs with the given `jobname`
    BATCHQ_QUEUE: str

    # Command to cancel all jobs with the given `jobname`
    BATCHQ_CANCEL: str

    # Location of job scratch directory
    jobfs: str

    # Maximum size of job array
    max_array_size: int

    def __call__(
        self,
        *,
        workflowname,
        batchdir,
        account,
        jobs,
        outfinaldir,
        failedjobfile,
        statefile,
        ctrlscripts,
    ):

        # Bundle jobs by order, dependencies, and resources
        job_bundles = dict()
        for job in jobs.values():
            job_bundle_ID = (
                "{needs_ID}_{time_min}_{mem_MiB}_{disk_MiB}_{threads}".format(**job)
            )
            job_bundle_key = (job["order"], job_bundle_ID)
            if job_bundle_key not in job_bundles:
                job_bundles[job_bundle_key] = list()
            job_bundles[job_bundle_key].append(job)

        # Split job groups into array of maximum size
        job_arrays = dict()
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
                job_array_ID = f"{job_bundle_ID}_{idx}"
                job_array_key = (job_bundle_order, job_array_ID)
                job_arrays[job_array_key] = job_array

        # Write job arrays
        log_make_job_arrays = ProgressLogger(_logger, "wrote %s job arrays in workflow")
        job_ID_to_job_array_ID_index = dict()
        for (_, job_array_ID), job_array in sorted(job_arrays.items()):

            # Make job array directory
            jobarraydir = job_dir_from_ID(batchdir / "job_arrays", job_array_ID)
            jobarraydir.mkdir(parents=True, exist_ok=False)

            # Use first job in array for retrieving resources
            job = job_array[0]

            # Write job array submission script
            jobarray_script_file = jobarraydir / "submit_job_array.sh"
            with FileLineWriter(jobarray_script_file) as f:
                f += "#!/bin/bash"

                # Write job script header
                f += self.BATCHQ_HEADER.format(
                    jobname=workflowname,
                    account=account,
                    time_min=job["time_min"],
                    mem_MiB=job["mem_MiB"],
                    disk_MiB=job["disk_MiB"],
                    threads=job["threads"],
                    stdout=jobarraydir / "job_stdout_%a",
                    stderr=jobarraydir / "job_stderr_%a",
                )

                # Exit on any errors
                f += "set -e"

                # Write shell functions
                # - fail() is a convenience function for reporting errors
                f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
"""

                # Change to job scratch directory
                f += f'test -d "{self.jobfs}" || mkdir -p "{self.jobfs}" || fail \'Could not make scratch directory {self.jobfs}\''
                f += f'cd "{self.jobfs}"'

                # Select which job in job array to run
                job_index = f"${{{self.BATCHQ_ARRAY_INDEX}}}"
                f += f"case {job_index} in"

                # Iterate over jobs in job array
                for idx, job in enumerate(job_array):
                    f += f"({idx})"

                    # Map individual job IDs to job array IDs and indexes
                    job_ID_to_job_array_ID_index[job["ID"]] = (job_array_ID, idx)

                    # Print comment used to extract job indexes and directories
                    f += f"# JOB {idx} {job['dir']}"

                    # Check that all prerequisite jobs have completed successfully
                    for needs_job_ID in job["needs"]:

                        # Path to file indicating job has completed successfully
                        needs_job_completed_file = (
                            jobs[needs_job_ID]["dir"] / "job_completed"
                        )

                        # Test that file exists
                        f += f"test -f '{needs_job_completed_file}'"

                    # Hard-link standard output/error files from job array back to job directory
                    for stdouterr in ("job_stdout", "job_stderr"):
                        jobarray_stdouterr_file = jobarraydir / (
                            stdouterr + "_" + job_index
                        )
                        job_stdouterr_file = job["dir"] / stdouterr
                        f += f'ln -f "{jobarray_stdouterr_file}" "{job_stdouterr_file}"'

                    # Copy input files into job scratch directory
                    for infile in job["infiles"]:
                        f += f"test -r '{infile}' || fail 'Missing input file {infile.name}'"
                        f += f"cp '{infile}' './{infile.name}'"

                    # Copy required executables into job scratch directory
                    for exc in [Path(cmd[0]) for cmd in job["cmds"]]:
                        f += f"test -r '{exc}' || fail 'Missing executable {exc.name}'"
                        f += f"cp '{exc}' './{exc.name}'"

                    # Run job script
                    f += "source ./job_script.sh"

                    # Copy output files out of job scratch directory
                    for outfile in job["outfiles"]:
                        f += f"test -r '{outfile.name}' || fail 'Missing output file {outfile.name}'"
                        f += f"cp './{outfile.name}' '{outfile}'"
                        if job["outfinal"]:
                            outfinaldir_file = outfinaldir / outfile.name
                            f += f"ln '{outfile}' '{outfinaldir_file}'"

                    # Create file indicating job has completed successfully
                    job_completed_file = job["dir"] / "job_completed"
                    f += f"date > '{job_completed_file}'"

                    f += ";;"

                # Handle an incorrect job array index
                max_job_index = len(job_array) - 1
                f += "(*)"
                f += f'fail "{self.BATCHQ_ARRAY_INDEX}={job_index} should be between 0 and {max_job_index}"'

                # end: Select which job in job array to run
                f += "esac"

                # Print comment used to extract job indexes and directories
                f += "# JOB END -"

            log_make_job_arrays.update()
        log_make_job_arrays.finish()

        # Write script to check all jobs have succeeded
        state_job_script_file = batchdir / "submit_state_job.sh"
        with FileLineWriter(state_job_script_file) as f:
            f += "#!/bin/bash"

            # Write job script header
            f += self.BATCHQ_HEADER.format(
                jobname=f"{workflowname}_state",
                account=account,
                time_min=15,
                mem_MiB=10,
                disk_MiB=0,
                threads=1,
                stdout=batchdir / "submit_state_job_stdout",
                stderr=batchdir / "submit_state_job_stderr",
            )

            # Exit on any errors
            f += "set -e"

            # Default to success
            f += "state=SUCCESS"

            # Check all jobs
            for _, job_array in job_arrays.items():
                for job in job_array:
                    job_completed_file = job["dir"] / "job_completed"
                    f += f"""
if test ! -r '{job_completed_file}'; then
    echo '{job['dir']}' >> '{failedjobfile}'
    state=FAILED
fi
"""
            # Write state
            f += f"echo \"${{state}}\" > '{statefile}'"

        # Write script to submit all jobs
        all_jobs_script_file = batchdir / "submit_all_jobs.sh"
        all_jobs_ID_written = set()
        with FileLineWriter(all_jobs_script_file) as f:
            f += "#!/bin/bash"

            # Write job script header
            f += self.BATCHQ_HEADER.format(
                jobname=workflowname,
                account=account,
                time_min=15,
                mem_MiB=10,
                disk_MiB=0,
                threads=1,
                stdout=batchdir / "submit_all_jobs_stdout",
                stderr=batchdir / "submit_all_jobs_stderr",
            )

            # Exit on any errors
            f += "set -e"

            # If errors are encountered, write to the state file
            f += f"""
function trap_state() {{
    local status=$?
    if test ${{status}} -ne 0; then
        echo FAILED > '{statefile}'
    fi
    exit ${{status}}
}}
trap trap_state EXIT
"""

            # Write shell functions
            # - fail() is a convenience function for reporting errors
            # - job_array_indexes() determines which indexes of an array
            #   job need to be run, and represents them as compactly as
            #   possible as an --array argument
            f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
function job_array_indexes() {
    local idxexpr=
    local idxlo=
    local idxhi=
    local idx=
    local jobdir=
    while read idx jobdir; do
        if test "X${idx}" != XEND; then
            jobcompleted="${jobdir}/job_completed"
            if test -r "${jobcompleted}"; then
                continue
            fi
            if test "X${idxlo}" = X; then
                idxlo=${idx}
                idxhi=${idx}
                continue
            fi
            if test $((idxhi+1)) -eq ${idx}; then
                idxhi=${idx}
                continue
            fi
        fi
        if test "X${idxexpr}" != X; then
            idxexpr="${idxexpr},"
        fi
        if test ${idxlo} -eq ${idxhi}; then
            idxexpr="${idxexpr}${idxlo}"
        else
            idxexpr="${idxexpr}${idxlo}-${idxhi}"
        fi
        idxlo=${idx}
        idxhi=${idx}
    done < <(sed -n 's/^# JOB //p' "$1")
    echo "${idxexpr}"
}
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

                    # Path to file indicating job has completed successfully
                    needs_job_completed_file = (
                        jobs[needs_job_ID]["dir"] / "job_completed"
                    )

                    # Map individual needed job ID to needed job array ID and index
                    needs_job_array_ID, needs_job_array_idx = (
                        job_ID_to_job_array_ID_index[needs_job_ID]
                    )

                    # Needed job array directory
                    needs_jobarraydir = job_dir_from_ID(
                        batchdir / "job_arrays", needs_job_array_ID
                    )

                    # Format dependency on needed job in job array
                    needs_job_in_array_batchqID = (
                        self.BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX.format(
                            job_array_batchID="${batchq_ID}",
                            job_array_index=needs_job_array_idx,
                        )
                    )

                    # Add batch system ID of prerequisite job to dependency
                    needs_jobarray_batchqID_file = (
                        needs_jobarraydir / "job_array_batchqID"
                    )
                    f += f"""
if test ! -f '{needs_job_completed_file}'; then
    test -r '{needs_jobarray_batchqID_file}' || fail 'Dependent job array {needs_job_array_ID} has not been submitted'
    batchq_ID=`cat '{needs_jobarray_batchqID_file}'`
    batchq_needs_IDs="${{batchq_needs_IDs}}:{needs_job_in_array_batchqID}"
fi
"""

                # Determine array indexes which should be submitted
                jobarray_script_file = jobarraydir / "submit_job_array.sh"
                f += f"batchq_array_indexes=`job_array_indexes {jobarray_script_file}`"

                # If jobs in array need to be run, submit them and save job ID
                # - jobs are initially submitted in a held state
                jobarray_batchqID_file = jobarraydir / "job_array_batchqID"
                f += f"""
if test "X${{batchq_array_indexes}}" = X; then
    batchq_ID=COMPLETED
else
    batchq_dependency="{self.BATCHQ_SUBMIT_ARG_DEPEND}afterok:${{{self.BATCHQ_JOB_ID}{self.BATCHQ_ID_REMOVE_SUFFIX}}}"
    if test "X${{batchq_needs_IDs}}" != X; then
        batchq_dependency="${{batchq_dependency}},{self.BATCHQ_SUBMIT_ARRAY_DEPEND}${{batchq_needs_IDs}}"
    fi
    batchq_ID=`{self.BATCHQ_SUBMIT_ARRAY} ${{batchq_dependency}} {self.BATCHQ_SUBMIT_ARG_ARRAY}${{batchq_array_indexes}} '{jobarray_script_file}'`
    batchq_ID=`echo "${{batchq_ID{self.BATCHQ_ID_REMOVE_SUFFIX}}}"`
    echo "info: submitted indexes ${{batchq_array_indexes}} of job array {job_array_ID}, batch ID ${{batchq_ID}}"
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
batchq_ID=`{self.BATCHQ_SUBMIT_STATE} ${{batchq_dependency}} '{state_job_script_file}'`
batchq_ID=`echo "${{batchq_ID{self.BATCHQ_ID_REMOVE_SUFFIX}}}"`
echo "info: submitted state job, batch ID ${{batchq_ID}}"
    """

        # Write commands to top-level submit script
        with FileLineWriter(ctrlscripts["submit"], append=True) as f:
            f += self.BATCHQ_SUBMIT_ALL.format(script=all_jobs_script_file)

        # Write commands to top-level queue script
        with FileLineWriter(ctrlscripts["queue"], append=True) as f:
            f += self.BATCHQ_QUEUE.format(jobname=workflowname)

        # Write commands to top-level cancel script
        with FileLineWriter(ctrlscripts["cancel"], append=True) as f:
            f += self.BATCHQ_CANCEL.format(jobname=workflowname)
