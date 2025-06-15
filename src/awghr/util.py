# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

import os
import re
import stat
import time
from pathlib import Path

from pint import UnitRegistry

_ureg = UnitRegistry()


class WorkflowException(Exception):
    """Raise for workflow generation errors"""


class ConfigException(Exception):
    """Raise for configuration errors"""


class BatchSystemException(Exception):
    """Raise for batch system errors"""


# Generate job directory from job ID:
# basedir
# └── subdirs 00/ to ff/ (max 256)
#     └── subdirs 00/ to ff/ (max 256)
#         └── subdirs of remaining job ID/
#             └── job files
def job_dir_from_ID(basedir: Path, job_ID: str):
    return basedir / job_ID[0:2] / job_ID[2:4] / job_ID[4:]


# List of job files, which may be indexed with regular expressions
class JobFileList:

    def __init__(self, _name, _list):
        self._name = _name
        self._list = _list

    def __repr__(self):
        if len(self._list) == 0:
            msg = f"'{self._name}' must return at least one file"
            raise WorkflowException(msg)
        return " ".join(self._list)

    def __getitem__(self, regex):
        f_lst = [x for x in self._list if re.match(regex, x)]
        if len(f_lst) == 0:
            msg = f"'{self._name}[{regex}]' must return at least one file"
            raise WorkflowException(msg)
        return " ".join(f_lst)


# Log progress at progressively longer intervals
class ProgressLogger:

    def __init__(self, logger, msg):
        self._logger = logger
        self._msg = msg
        self._count = 0
        self._t0 = time.time()
        self._dt_print_prog = 5
        self._ddt_print_prog = 5

    def update(self):
        self._count += 1
        dt = time.time() - self._t0
        if dt > self._dt_print_prog:  # pragma: no cover
            r = (self._count / _ureg.Quantity(dt, "second")).to_compact()
            self._logger.info(self._msg + " (%s)", str(self._count), format(r, "0.1f~"))
            self._ddt_print_prog += 5
            self._dt_print_prog += self._ddt_print_prog

    def finish(self):
        dt = time.time() - self._t0
        r = (self._count / _ureg.Quantity(dt, "second")).to_compact()
        self._logger.info(
            self._msg + " (%s)",
            "a total of " + str(self._count),
            format(r, "0.1f~"),
        )


# Convenience class for writing lines to a text file
class FileLineWriter:

    def __init__(self, filepath: Path, *, executable: bool = False):
        self._filepath = filepath
        self._finalmode = (stat.S_IRUSR | stat.S_IXUSR) if executable else stat.S_IRUSR

    def __enter__(self):
        if self._filepath.is_file():
            os.chmod(self._filepath, stat.S_IRUSR | stat.S_IWUSR)
        self._file = self._filepath.open("w")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del self._file
        os.chmod(self._filepath, self._finalmode)

    def __iadd__(self, line: str):
        print(str(line).strip("\n"), file=self._file)
        return self
