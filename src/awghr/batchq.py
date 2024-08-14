import logging
import math
from typing import Any, Dict

from .util import FileLineWriter, ProgressLogger, job_dir_from_ID

_logger = logging.getLogger(__name__)


# Generic workflow writer for PBS/Slurm/etc. style batch systems
class BatchQWorkflowWriter:

    # Configuration keys for generic workflow
    KEYS: Dict[str, Any] = {
        "jobfs": None,
        "n_arraymax": 0,
    }

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
        submittedfile,
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
            jobarraydir.mkdir(parents=True, exist_ok=True)

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
                    stdout=jobarraydir
                    / f"job_stdout_{self.BATCHQ_SUBMIT_ARRAY_INDEX_REPL}",
                    stderr=jobarraydir
                    / f"job_stderr_{self.BATCHQ_SUBMIT_ARRAY_INDEX_REPL}",
                )

                # Exit on any errors
                f += "set -e"

                # Write shell function fail() for reporting errors
                f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
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
                f += f'test -d "{self.jobfs}" || mkdir -p "{self.jobfs}" || fail \'Could not make scratch directory {self.jobfs}\''
                f += f'cd "{self.jobfs}"'

                # Select which job in job array to run
                f += "case ${batchq_job_index} in"

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
                            stdouterr + "_" + batch_index
                        )
                        job_stdouterr_file = job["dir"] / stdouterr
                        f += f'ln -f "{jobarray_stdouterr_file}" "{job_stdouterr_file}"'

                    # Copy input files into job scratch directory
                    for infile in job["infiles"]:
                        f += f"test -f '{infile}' || fail 'Missing input file {infile.name}'"
                        f += f"cp '{infile}' './{infile.name}'"

                    # Run job script
                    f += "source ./job_script.sh"

                    # Copy output files out of job scratch directory
                    for outfile in job["outfiles"]:
                        f += f"test -f './{outfile.name}' || fail 'Missing output file {outfile.name}'"
                        f += f"cp './{outfile.name}' '{outfile}'"
                        if job["outfinal"]:
                            outfinaldir_file = outfinaldir / outfile.name
                            f += f"test ! -r '{outfinaldir_file}' || fail 'Output file {outfile.name} already exists in {outfinaldir}'"
                            f += f"ln -f '{outfile}' '{outfinaldir_file}'"

                    # Create file indicating job has completed successfully
                    job_completed_file = job["dir"] / "job_completed"
                    f += f"date > '{job_completed_file}'"

                    f += ";;"

                # Handle an incorrect job array index
                f += "(*)"
                f += "fail \"Incorrect job array index '${batchq_job_index}'\""

                # end: Select which job in job array to run
                f += "esac"

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

            # Remove file indicating workflow has been submitted
            f += f"rm -f '{submittedfile}'"

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

            # Write shell function fail() for reporting errors
            f += """
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
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
        jobcompleted="${jobdir}/job_completed"
        if test ! -r "${jobcompleted}"; then
            batchq_array_indexes="${batchq_array_indexes} ${idx}"
            : $((++batchq_array_count))
        fi
    done < <(sed -n 's/^# JOB //p' "$1")
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

                    # Add batch system ID of prerequisite job to dependency
                    needs_jobarray_batchqID_file = (
                        needs_jobarraydir / "job_array_batchqID"
                    )
                    needs_jobarray_index_file = needs_jobarraydir / "job_array_indexes"
                    f += f"""
if test ! -f '{needs_job_completed_file}'; then
    test -f '{needs_jobarray_batchqID_file}' || fail 'Dependent job array {needs_job_array_ID} has not been submitted'
    batchq_ID=`cat '{needs_jobarray_batchqID_file}'`
    job_array_needs_ID_idx "${{batchq_ID}}" '{needs_jobarray_index_file}' '{needs_job_array_idx}'
    batchq_needs_IDs="${{batchq_needs_IDs}}:${{batchq_needs_ID_idx}}"
fi
"""

                # Determine job array indexes which should be submitted
                jobarray_script_file = jobarraydir / "submit_job_array.sh"
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
    batchq_ID=COMPLETED
else
    batchq_dependency="{self.BATCHQ_SUBMIT_ARG_DEPEND}afterok:${{{self.BATCHQ_JOB_ID}{self.BATCHQ_ID_REMOVE_SUFFIX}}}"
    if test "X${{batchq_needs_IDs}}" != X; then
        batchq_dependency="${{batchq_dependency}},{self.BATCHQ_SUBMIT_ARRAY_DEPEND}${{batchq_needs_IDs}}"
    fi
    rm -f '{jobarraydir}/"job_stdout_'* '{jobarraydir}/"job_stderr_'*
    batchq_ID=`{self.BATCHQ_SUBMIT_ARRAY} ${{batchq_dependency}} {self.BATCHQ_SUBMIT_ARG_ARRAY}${{batchq_array}} '{jobarray_script_file}'`
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
batchq_ID=`{self.BATCHQ_SUBMIT_STATE} ${{batchq_dependency}} '{state_job_script_file}'`
batchq_ID=`echo "${{batchq_ID{self.BATCHQ_ID_REMOVE_SUFFIX}}}"`
echo "info: submitted state job, batch ID ${{batchq_ID}}"
    """

        # Write commands to top-level control scripts
        ctrlscripts["submit"] += self.BATCHQ_SUBMIT_ALL.format(
            script=all_jobs_script_file
        )
        ctrlscripts["queue"] += self.BATCHQ_QUEUE.format(jobname=workflowname)
        ctrlscripts["cancel"] += self.BATCHQ_CANCEL.format(jobname=workflowname)
