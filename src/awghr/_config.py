# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Workflow generator configuration."""

import argparse
import os
import re
import sys
from configparser import ConfigParser
from pathlib import Path
from shutil import which
from socket import getfqdn
from typing import Any, Dict, List, Optional, Set, Tuple

from ._htcondor import HTCondorWorkflowWriter
from ._local import LocalWorkflowWriter
from ._pbs import PBSWorkflowWriter
from ._slurm import SlurmWorkflowWriter
from ._util import LOGGER, ConfigException, WorkflowWriter

# Known batch systems:
# - name of batch system
# - description of batch system
# - characteristic executable used to identify batch system
# - function which writes workflow specific to batch system
_batch_systems: List[Tuple[str, str, str, type[WorkflowWriter]]] = [
    # Local workflow writer must appear first
    ("local", "Run workflow on local computer", "bash", LocalWorkflowWriter),
    # Other batch systems
    ("htcondor", "HTCondor", "condor_q", HTCondorWorkflowWriter),
    ("pbs", "Portable Batch System and its derivatives", "qmgr", PBSWorkflowWriter),
    ("slurm", "Slurm Workload Manager", "scontrol", SlurmWorkflowWriter),
]

# General configuration keys and their default values
_general_cfg: Dict[str, Any] = {
    "account": None,
    "basedir": Path.cwd(),
    "cluster": "default",
    "dated?": False,
    "envlist": None,
}

# Valid characters which general configuration values may contain
_general_cfg_valid_chars: Dict[str, str] = {"cluster": "a-z"}

# Override host name for tests in test_02_config.py
_override_host = None

# List of all cluster names in configuration file
cluster_names: Set[str] = set()


def host_name():
    """Get host name."""
    return getfqdn() if _override_host is None else _override_host


def batch_system_names():
    """Names of batch systems."""
    return set(batch_system for batch_system, _, _, _ in _batch_systems)


def read_config(
    src: Optional[str] = None,
    tags: Optional[List[str]] = None,
    select_batch_system: Optional[str] = None,
):
    """Read configuration."""

    cfg: Dict[str, Any] = {}
    batch_system_cfg: Dict[str, Any] = {}

    # Determine batch system
    batch_keys = {}
    for batch_system, _, batch_exec, workflow_writer in _batch_systems:
        if which(batch_exec) is not None and (
            select_batch_system is None or select_batch_system == batch_system
        ):
            # Local workflow writer may be overridden by other batch systems
            if "batch_system" not in cfg or cfg["batch_system"] == "local":
                cfg["batch_system"] = batch_system
                cfg["workflow_writer"] = workflow_writer
                if workflow_writer is not None:
                    for cls in workflow_writer.mro():
                        if hasattr(cls, "KEYS"):
                            for batch_key, batch_key_val in getattr(
                                cls, "KEYS"
                            ).items():
                                assert batch_key not in batch_keys
                                batch_keys[batch_key] = batch_key_val
            else:
                msg = f"both '{cfg['batch_system']}' and '{batch_system}' batch systems appear to be present"
                raise ConfigException(msg)
    if "batch_system" not in cfg:
        msg = f"batch system '{select_batch_system}' not found"
        raise ConfigException(msg)

    # Read configuration
    parser = ConfigParser()
    if src is None:
        if "AWGHR_CFGFILE" in os.environ:
            src_file = Path(os.environ["AWGHR_CFGFILE"])
        else:
            src_file = Path(os.environ["HOME"]) / ".awghr.cfg"
        LOGGER.info(f"reading configuration from '{src_file}'")
        parser.read(src_file, encoding="utf-8")
    else:
        LOGGER.info("reading configuration from string")
        parser.read_string(src)

    # Parse sections
    for section in parser:
        use_section = True
        batch_system_section = None
        if section != parser.default_section:

            # Read `match` keys from configuration
            for key in parser[section]:
                if not key.startswith("match "):
                    continue
                _, target = key.split(maxsplit=1)
                match = parser[section][key]

                if target == "host":
                    host = host_name()

                    # `match` starting with "." must match end of host name
                    # `match` containing "." (but not at start/end) must match whole host name
                    # Otherwise `match` must match start of host name
                    if match.startswith("."):
                        if not host.endswith(match):
                            use_section = False
                    elif (
                        "." in match
                        and not match.startswith(".")
                        and not match.endswith(".")
                    ):
                        if host != match:
                            use_section = False
                    else:
                        if not host.startswith(match):
                            use_section = False

                elif target == "batch":

                    # `match` must be a known match system
                    if match not in batch_system_names():
                        msg = f"unknown batch system '{match}' in section '{section}'"
                        raise ConfigException(msg)
                    if match == cfg["batch_system"]:
                        batch_system_section = cfg["batch_system"]
                    else:
                        use_section = False

                elif target == "tag":

                    # If `tags` is empty, do not match any tagged section
                    if tags is None:
                        use_section = False

                    # `match` must be in `tags` (ignoring case)
                    else:
                        tags_lower = {tag.lower() for tag in tags}
                        if match.lower() not in tags_lower:
                            use_section = False

                else:
                    msg = f"unknown section target '{target}'"
                    raise ConfigException(msg)

        # Read non-`match` keys from configuration
        key_cfg = {}
        for key in parser[section]:
            if key.startswith("match "):
                continue

            # Parse value of key and assign to dictionary
            val: Any
            if key.endswith("?"):
                try:
                    val = parser.getboolean(section, key)
                except ValueError:
                    val = parser[section][key]
                    msg = f"section key value '[{section}].{key}'='{val}' is not a valid boolean"
                    raise ConfigException(msg)
                key_cfg[key] = val

            elif key.startswith("n_"):
                try:
                    val = parser.getint(section, key)
                except ValueError:
                    val = parser[section][key]
                    msg = f"section key value '[{section}].{key}'='{val}' is not a valid integer"
                    raise ConfigException(msg)
                key_cfg[key] = val

            elif key.endswith("dir"):
                val = Path(parser[section][key])
                try:
                    val = val.expanduser().resolve(strict=False)
                except (FileNotFoundError, RuntimeError):
                    msg = f"section key value '[{section}].{key}'='{val}' is not a valid path"
                    raise ConfigException(msg)
                key_cfg[key] = val

            elif key.endswith("list"):
                val = parser[section][key].strip().split("\n")
                key_cfg[key] = val

            else:
                val = str(parser[section][key])
                if key in _general_cfg_valid_chars:
                    valid_chars = _general_cfg_valid_chars[key]
                    invalid_char_regex = r"[^" + valid_chars + r"]"
                    if re.search(invalid_char_regex, val):
                        msg = f"section key value '[{section}].{key}'='{val}' may only characters '{valid_chars}'"
                        raise ConfigException(msg)
                key_cfg[key] = val

        # Store list of all cluster names in configuration file
        if "cluster" in key_cfg:
            cluster = key_cfg["cluster"]
            if cluster in batch_system_names():
                msg = f"cluster name '{cluster}' cannot be the name of a batch system"
                raise ConfigException(msg)
            cluster_names.add(cluster)

        if use_section:

            for key, val in key_cfg.items():

                # Check for valid key
                if key not in _general_cfg and (
                    batch_system_section is None or key not in batch_keys
                ):
                    msg = f"unknown section key '[{section}].{key}'"
                    raise ConfigException(msg)

                # Decide which dictionary to add key to
                if key in _general_cfg:
                    add_to_cfg = cfg
                else:
                    add_to_cfg = batch_system_cfg
                add_to_val = add_to_cfg.get(key, None)
                if isinstance(add_to_val, list):

                    # Append values to existing list
                    for v in val:
                        if v not in add_to_val:
                            add_to_val.append(v)
                    add_to_cfg[key] = add_to_val

                else:

                    # Store scalar value
                    add_to_cfg[key] = val

    # Use default configuration values
    for key in _general_cfg:
        if key not in cfg:
            cfg[key] = _general_cfg[key]

    # Check that batch system-specific keys are present
    for key in batch_keys:
        if key not in batch_system_cfg:
            batch_system_cfg[key] = batch_keys[key]
            if batch_system_cfg[key] is None:
                msg = f"{cfg['batch_system']}-specific configuration key '{key}' is undefined"
                raise ConfigException(msg)

    # Store batch system-specific configuration
    cfg["batch_system_cfg"] = batch_system_cfg

    return cfg


def print_aligned(d):
    """Helper function to print a dictionary with alignment."""
    m = max(len(k) for k in d.keys())
    for k, v in sorted(d.items()):
        vs = "<undefined>" if v is None else str(v)
        print(f"    {k:<{m}} : {vs}")


def cli(*argv: str):
    """Command-line interface: show configuration."""

    # Build command-line argument parser
    parser = argparse.ArgumentParser(
        description="show configuration",
    )
    parser.add_argument("--tags", type=str, nargs="+", help="assume these tags")
    parser.add_argument(
        "--batch",
        metavar="BATCH_SYSTEM",
        type=str,
        default=None,
        help="assume this batch system",
    )

    # Parse command line
    args = parser.parse_args(argv if len(argv) > 0 else sys.argv[1:])

    # Read configuration
    cfg = read_config(tags=args.tags, select_batch_system=args.batch)

    # Print supported batch systems
    print("Supported batch systems:")
    print_aligned(
        {
            batch_system: batch_system_desc
            for batch_system, batch_system_desc, _, _ in _batch_systems
            + [("local", "Runs workflow on local computer", "", None)]
        }
    )

    # Show general configuration
    host = host_name()
    tags = ",".join(args.tags) if args.tags is not None else "<none>"
    print(f"General configuration for host={host}, tags={tags}:")
    print_aligned({k: cfg[k] for k in _general_cfg})

    # Show batch system configuration
    batch = cfg["batch_system"]
    print(f"Batch system configuration for batch={batch}:")
    if cfg["batch_system_cfg"]:
        print_aligned(cfg["batch_system_cfg"])
    else:
        print("    <none>")

    return 0
