# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""PBS workflows."""

import subprocess
from typing import Any, Dict, Tuple

from ._batchq import BatchQWorkflowWriter
from ._util import BatchSystemException


class PBSWorkflowWriter(BatchQWorkflowWriter):
    """Write PBS workflow."""

    # PBS-specific configuration keys
    KEYS: Dict[str, Any] = {}

    BATCHQ_HEADER = """
    #PBS -N {jobname}
    #PBS -A {account}
    #PBS -l walltime={time_min}:00
    #PBS -l mem={mem_MB}MB
    #PBS -l ncpus={ncpus}
    #PBS -o {stdout}
    #PBS -e {stderr}
    #PBS -k doe
    """

    BATCHQ_HEADER_NGPUS = "#PBS -l gpus={n}"

    BATCHQ_HEADER_COMMENT = "# comment: {comment}"

    BATCHQ_JOB_ID = "PBS_JOBID"

    BATCHQ_JOB_ARRAY_ID_EXPR = "${PBS_JOBID}"

    BATCHQ_ARRAY_INDEX = "PBS_ARRAY_INDEX"

    # - PBS insists that job arrays must have more than one subjob ...
    #   which we can get around by using a step larger than the range
    BATCHQ_ARRAY_SINGLETON = "0-1:2"

    BATCHQ_ARRAY_RANGE = "0-{n}"

    # - PBS does not support `{job_array_index}`, i.e. depending on job array
    #   subjobs. Everything still works since job arrays always contain jobs of
    #   the same dependency order, but the workflow will likely run slower,
    #   because it will have to wait for a whole job array to finish before
    #   running its dependents
    BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX = "{batch_ID}"

    BATCHQ_SUBMIT_ARG_DEPEND = "-W depend="

    BATCHQ_SUBMIT_ARG_ARRAY = "-J "

    BATCHQ_SUBMIT_ARRAY_INDEX_REPL = "^array_index^"

    # - PBS provides no method to kill jobs with an unsatisfied dependency
    #   (because a prerequisite job failed), so must use `afterok` for
    #   prerequisite jobs here. This still works because dependent jobs will be
    #   run even if a prerequisite job fails (because of `afterany`) but will
    #   immediately fail if the `job_state` files for their prerequisites do not
    #   indicate SUCCESS. So the workflow will still fail gracefully, but
    #   slower, because all jobs will have to be run.
    BATCHQ_SUBMIT_ARRAY = "qsub"
    BATCHQ_SUBMIT_ARRAY_DEPEND = "afterany"

    BATCHQ_SUBMIT_STATE = "qsub"
    BATCHQ_SUBMIT_STATE_DEPEND = "afterany"

    # `qsub` returns `<job_ID>.<server>` for single jobs, and
    # `<job_array_ID>[].<server>` for job arrays. `<server>` is
    # optional and is removed for brevity.
    BATCHQ_ID_REMOVE_SUFFIX = "%%.*"

    def __init__(self, *, batchsyscfg):
        super().__init__(batchsyscfg=batchsyscfg)

        # Store location of job scratch directory
        self.jobfs = batchsyscfg["jobfs"]

        # Store maximum size of PBS job array
        if batchsyscfg["n_arraymax"] > 0:
            self.max_array_size = batchsyscfg["n_arraymax"]
        else:
            # Run `qmgr -c 'print server'` and parse its output
            retn = subprocess.run(
                ["qmgr", "-c", "print server"], capture_output=True, check=True
            )
            pbs_cfg = {}
            for line in retn.stdout.decode("utf-8").splitlines():
                prefix = "set server "
                if line.startswith(prefix):
                    key, val = line[len(prefix) :].split("=", maxsplit=1)
                    pbs_cfg[key.strip()] = val.strip()

            # Get maximum size of PBS job array
            try:
                self.max_array_size = int(pbs_cfg["max_array_size"])
            except KeyError:  # pragma: no cover
                msg = "could not determine maximum size of PBS job array"
                raise BatchSystemException(msg)

    def batchq_ctrl_commands(self) -> Tuple[str, str, str]:
        """Get top-level batch control script commands."""

        # Command to submit workflow scripts in $@
        submit = """
        batchq_submit() {
            local script=
            for script in $@; do
                qsub ${script} >/dev/null
            done
        }
        """

        # Command to display all queued jobs with names in $@
        queue = """
        batchq_queue() {
            local allbatchIDs=
            local jobname=
            for jobname in $@; do
                local batchIDs=`qselect -N ${jobname}`
                if test "X${batchIDs}" != X; then
                    allbatchIDs="${allbatchIDs} ${batchIDs}"
                fi
            done
            if test "X${allbatchIDs}" != X; then
                qstat -w -T ${allbatchIDs} 2>/dev/null || true
            fi
        }
        """

        # Command to cancel all jobs with names in $@
        cancel = """
        batchq_cancel() {
            local allbatchIDs=
            local jobname=
            for jobname in $@; do
                local batchIDs=`qselect -N ${jobname}`
                if test "X${batchIDs}" != X; then
                    allbatchIDs="${allbatchIDs} ${batchIDs}"
                fi
            done
            if test "X${allbatchIDs}" != X; then
                qdel ${allbatchIDs} || true
            fi
        }
        """

        return submit, queue, cancel
