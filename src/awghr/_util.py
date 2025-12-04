# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Utility functions."""

import os
import re
import stat
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Tuple

from pint import Quantity


class WorkflowException(Exception):
    """Raise for workflow generation errors."""


class ConfigException(Exception):
    """Raise for configuration errors."""


class BatchSystemException(Exception):
    """Raise for batch system errors."""


class WorkflowWriter(ABC):
    """Workflow writer base class."""

    def __init__(self, *, batchsyscfg) -> None:
        """Initialise workflow writer."""
        pass

    @abstractmethod
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

    @abstractmethod
    def get_ctrl_commands(self, *, workflowname, basebatchdir) -> Tuple[str, str, str]:
        """Get top-level control script commands."""


def job_dir_from_ID(basedir: Path, job_ID: str):
    """Generate job directory from job ID.

    Job directory structure:
    ```
    basedir
    └── subdirs 00/ to ff/ (max 256)
        └── subdirs 00/ to ff/ (max 256)
            └── subdirs of remaining job ID/
                └── job files
    ```
    """
    return basedir / job_ID[0:2] / job_ID[2:4] / job_ID[4:]


class JobFileList:
    """List of job files, which may be indexed with regular expressions."""

    def __init__(self, _name, _list):
        """Initialise job file list."""
        self._name = _name
        self._list = _list

    def __repr__(self):
        if len(self._list) == 0:
            msg = f"'{self._name}' must return at least one file"
            raise WorkflowException(msg)
        return " ".join(self._list)

    def __getitem__(self, arg):
        if ":" in arg:
            regex, sep = arg.split(":", maxsplit=1)
        else:
            regex = arg
            sep = " "
        f_lst = [x for x in self._list if re.match(regex, x)]
        if len(f_lst) == 0:
            msg = f"'{self._name}[{regex}]' must return at least one file"
            raise WorkflowException(msg)
        return sep.join(f_lst)


class ProgressLogger:
    """Log progress at progressively longer intervals."""

    def __init__(self, items=None, *, logger, msg):
        """Initialise progress logger."""
        if items is not None:
            self._items = list(items)
            self._total = len(items)
        else:
            self._total = 0
        self._logger = logger
        self._msg = msg
        self._count = 0
        self._t0 = time.time()
        self._dt_print_prog = 5
        self._ddt_print_prog = 5

    def update(self):
        """Update log progress."""
        self._count += 1
        dt = time.time() - self._t0
        if dt > self._dt_print_prog:  # pragma: no cover
            r = (self._count / Quantity(dt, "second")).to_compact()
            self._logger.info(self._msg + " (%s)", str(self._count), format(r, "0.1f~"))
            self._ddt_print_prog += 5
            self._dt_print_prog += self._ddt_print_prog

    def finish(self):
        """Finish log progress."""
        dt = time.time() - self._t0
        r = (self._count / Quantity(dt, "second")).to_compact()
        self._logger.info(
            self._msg + " (%s)",
            "a total of " + str(self._count),
            format(r, "0.1f~"),
        )

    def __iter__(self):
        """Use as an iterator over `items`."""
        return self

    def __next__(self):
        """Get next item in `items`."""
        assert self._items is not None
        self.update()
        if self._count > self._total:
            raise StopIteration
        else:
            return self._items[self._count - 1]


class FileLineWriter:
    """Convenience class for writing lines to a text file."""

    def __init__(self, filepath: Path, *, executable: bool = False):
        """Initialise file line writer."""
        self._filepath = filepath
        self._finalmode = (stat.S_IRUSR | stat.S_IXUSR) if executable else stat.S_IRUSR
        self._prefix = ""

    def __enter__(self):
        """Open file."""
        if self._filepath.is_file():
            os.chmod(self._filepath, stat.S_IRUSR | stat.S_IWUSR)
        self._file = self._filepath.open("w")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close file."""
        del self._file
        os.chmod(self._filepath, self._finalmode)

    @contextmanager
    def indent(self, indent: int):
        """Print lines with a given indentation."""
        old_prefix = self._prefix
        self._prefix = " " * indent
        try:
            yield
        finally:
            self._prefix = old_prefix

    def __iadd__(self, lines: str):
        """Print lines to file, stripping out common indentation."""
        lines = dedent(str(lines)).strip("\n")
        lines = "\n".join(self._prefix + line.rstrip() for line in lines.split("\n"))
        print(lines, file=self._file)
        return self
