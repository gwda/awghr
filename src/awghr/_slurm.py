# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Slurm workflows."""

import subprocess
from typing import Any, Dict, Tuple

from ._batchq import BatchQWorkflowWriter
from ._util import BatchSystemException


class SlurmWorkflowWriter(BatchQWorkflowWriter):
    """Write Slurm workflow."""

    # Slurm-specific configuration keys
    KEYS: Dict[str, Any] = {}

    BATCHQ_HEADER = """
    #SBATCH --job-name={jobname}
    #SBATCH --account={account}
    #SBATCH --time={time_min}:00
    #SBATCH --mem={mem_MB}M
    #SBATCH --tmp={disk_MB}M
    #SBATCH --ntasks=1
    #SBATCH --cpus-per-task={ncpus}
    #SBATCH --output={stdout}
    #SBATCH --error={stderr}
    #SBATCH --signal=B:TERM@30
    """

    BATCHQ_HEADER_NGPUS = "#SBATCH --gres=gpu:{n}"

    BATCHQ_HEADER_COMMENT = '''#SBATCH --comment="{comment}"'''

    BATCHQ_JOB_ID = "SLURM_JOB_ID"

    BATCHQ_JOB_ARRAY_ID_EXPR = "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"

    BATCHQ_ARRAY_INDEX = "SLURM_ARRAY_TASK_ID"

    BATCHQ_ARRAY_SINGLETON = "0"

    BATCHQ_ARRAY_RANGE = "0-{n}"

    # - Slurm can run without `{batch_index}`, since job arrays
    #   always contain jobs of the same dependency order, but the
    #   workflow will likely run slower, because it will have to wait
    #   for a whole job array to finish before running its dependents
    BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX = "{batch_ID}_{batch_index}"

    BATCHQ_SUBMIT_ARG_DEPEND = "--dependency="

    BATCHQ_SUBMIT_ARG_ARRAY = "--array="

    BATCHQ_SUBMIT_ARRAY_INDEX_REPL = "%a"

    # - Slurm's `--kill-on-invalid-dep=yes` option will kill jobs with
    #   an unsatisfied dependency (because a prerequisite job failed),
    #   so it's safe to use `afterok` for prerequisite jobs here
    # - Slurm can also be run without this option, i.e.:
    #     BATCHQ_SUBMIT_ARRAY = "sbatch --parsable"
    #     BATCHQ_SUBMIT_ARRAY_DEPEND = "afterany"
    #   This still works because dependent jobs will be run even if a
    #   prerequisite job fails (because of `afterany`) but will
    #   immediately fail if the `job_state` files for their
    #   prerequisites do not indicate SUCCESS. So the workflow will
    #   still fail gracefully, but slower, because all jobs will have
    #   to be run.
    BATCHQ_SUBMIT_ARRAY = "sbatch --parsable --kill-on-invalid-dep=yes"
    BATCHQ_SUBMIT_ARRAY_DEPEND = "afterok"

    BATCHQ_SUBMIT_STATE = "sbatch --parsable --kill-on-invalid-dep=yes"
    BATCHQ_SUBMIT_STATE_DEPEND = "afterany"

    # - `sbatch --parsable` returns `<job_ID>[:<cluster_name>]`.
    # `<cluster_name>` is not part of the job ID and must be removed.
    BATCHQ_ID_REMOVE_SUFFIX = "%%:*"

    BATCHQ_SUBMIT_ALL = "sbatch"

    def __init__(self, *, batchsyscfg):
        super().__init__(batchsyscfg=batchsyscfg)

        # Store location of job scratch directory
        self.jobfs = batchsyscfg["jobfs"]

        # Store maximum size of Slurm job array
        if batchsyscfg["n_arraymax"] > 0:
            self.max_array_size = batchsyscfg["n_arraymax"]
        else:
            # Run `scontrol show config` and parse its output
            retn = subprocess.run(
                ["scontrol", "show", "config"], capture_output=True, check=True
            )
            slurm_cfg = {}
            for line in retn.stdout.decode("utf-8").splitlines():
                if "=" in line:
                    key, val = line.split("=", maxsplit=1)
                    slurm_cfg[key.strip()] = val.strip()

            # Get maximum size of Slurm job array
            try:
                self.max_array_size = int(slurm_cfg["MaxArraySize"])
            except KeyError:  # pragma: no cover
                msg = "could not determine maximum size of Slurm job array"
                raise BatchSystemException(msg)

    def batchq_ctrl_commands(self) -> Tuple[str, str, str]:
        """Get top-level batch control script commands."""

        # Command to submit workflow scripts in $@
        submit = """
        batchq_submit() {
            local script=
            for script in $@; do
                sbatch --quiet ${script}
            done
        }
        """

        # Command to display all queued jobs with names in $@
        queue = """
        batchq_queue () {
            local jobnames=`echo $@ | tr ' ' ','`
            local jobcounts=`squeue --name="${jobnames}" --noheader --array --format='%T' --sort='T' | uniq -c`
            if test "X${jobcounts}" != X; then
                echo ${jobcounts}
            fi
            squeue --name="${jobnames}" --format='%20i  %2t  %.12M  %.12L  %.10m  %.12d  %.4C  %.8p  %12P  %R' --sort='-M,-p'
        }
        """

        # Command to cancel all jobs with names in $@
        cancel = """
        batchq_cancel() {
            local jobname=
            for jobname in $@; do
                scancel --name="${jobname}"
            done
        }
        """

        return submit, queue, cancel
