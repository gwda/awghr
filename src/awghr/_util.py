# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Utility functions."""

import logging
import math
import os
import pickle
import re
import stat
import sys
import time
from abc import ABC, abstractmethod
from collections import UserDict
from contextlib import contextmanager
from hashlib import blake2b
from pathlib import Path
from textwrap import dedent
from typing import Tuple

from pint import Quantity
from tqdm import tqdm

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
LOGGER = logging.getLogger("awghr")


class WorkflowException(Exception):
    """Raise for workflow generation errors."""


class ConfigException(Exception):
    """Raise for configuration errors."""


class BatchSystemException(Exception):
    """Raise for batch system errors."""


# Common script header:
# - Exit on any script errors
# - Shell function fail() for reporting errors
# - Shell function write_atomic() to write status files atomically
SCRIPT_HEADER = """
set -e
function fail() {
    echo 'FAIL:' "$@" >&2
    exit 1
}
function write_atomic() {
    local status="$1"
    local status_file="$2"
    echo "${status}" > "${status_file}.tmp"
    mv -f "${status_file}.tmp" "${status_file}"
}
"""


def resolved_path(value):
    """Resolve a path."""
    return Path(value).resolve(strict=True)


def file_hash(path: Path):
    h = blake2b(digest_size=8, usedforsecurity=False)
    with path.open("rb") as f:
        for b in iter(lambda: f.read(2**16), b""):
            h.update(b)
    return h.hexdigest()


def write_atomic(status: str, status_file: Path):
    """Python function to write status files atomically."""
    status_file_tmp = status_file.with_suffix(".tmp")
    status_file_tmp.write_text(status + "\n")
    status_file_tmp.replace(status_file)


@contextmanager
def modify_read_only_directory(*dirs: Path):
    """Set permissions to modify read-only directories."""
    try:
        for d in dirs:
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        yield
    finally:
        for d in dirs:
            if d.is_dir():
                os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)


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


def request_disk_for_external_input_files(jobs):
    """Compute sizes of external input files and add to requested temporary disk space."""

    # Get list of all external input files
    all_extinfiles = set()
    for job in jobs:
        all_extinfiles.update(job["extinfiles"])

    # Compute sizes of external input files
    extinfile_size = {}
    for extinfile in tqdm(
        all_extinfiles, desc="Computing input file sizes ", disable=None, leave=False
    ):
        extinfile_size[extinfile] = Quantity(extinfile.stat().st_size, "byte")

    for job in jobs:

        # Compute size of all external input files for this job
        job_extinfile_size = sum(
            [extinfile_size[extinfile] for extinfile in job["extinfiles"]]
        )
        job_extinfile_size_MB = job_extinfile_size.to("MB").magnitude

        # Round size up to 2 significant figures
        d = 10 ** (math.floor(math.log10(job_extinfile_size_MB)) - 1)
        job_extinfile_size_MB = math.ceil(job_extinfile_size_MB / d) * d

        # Add to requested temporary disk space
        job["disk_MB"] += job_extinfile_size_MB


class WorkflowWriter(ABC):
    """Workflow writer base class."""

    def __init__(self, *, batchsyscfg) -> None:
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
        skip_exit_code,
    ) -> None:
        """Write workflow."""

    @abstractmethod
    def get_ctrl_commands(self, *, workflowname) -> Tuple[str, str, str]:
        """Get top-level control script commands."""


class JobFileList:
    """List of job files, which may be indexed with regular expressions."""

    def __init__(self, _name, _list):
        self._name = _name
        self._list = _list

    def __repr__(self):
        if len(self._list) == 0:
            msg = f"'{self._name}' must return at least one file"
            raise WorkflowException(msg)
        return " ".join("./" + x for x in sorted(self._list))

    def __getitem__(self, arg):
        if ":" in arg:
            regex, sep = arg.split(":", maxsplit=1)
        else:
            regex = arg
            sep = " "
        regex = re.compile(regex)
        filtered_list = [x for x in self._list if re.search(regex, x) is not None]
        if len(filtered_list) == 0:
            msg = f"'{self._name}[{regex}:{sep}]' must return at least one file"
            raise WorkflowException(msg)
        return sep.join("./" + x for x in sorted(filtered_list))


class ProgressLogger:
    """Log progress at progressively longer intervals."""

    def __init__(self, items=None, *, msg):
        if items is not None:
            self._items = list(items)
            self._total = len(items)
        else:
            self._total = 0
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
            LOGGER.info(self._msg + " (%s)", str(self._count), format(r, "0.1f~"))
            self._ddt_print_prog += 5
            self._dt_print_prog += self._ddt_print_prog

    def finish(self):
        """Finish log progress."""
        dt = time.time() - self._t0
        r = (self._count / Quantity(dt, "second")).to_compact()
        LOGGER.info(
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
        self._prefix += " " * indent
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


class DictCache(UserDict):
    """Load and save a dictionary to a cache file."""

    def __init__(self, cache_file: Path):
        super().__init__()
        self._cache_file = cache_file

    def __enter__(self):
        """Load cache."""
        if self._cache_file.is_file():
            self.data = pickle.load(self._cache_file.open("rb"))

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Save cache."""
        pickle.dump(self.data, self._cache_file.open("wb"))
