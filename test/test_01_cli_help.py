# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test command-line interface help."""

from awghr._config import cli as config_cli
from awghr._manage import cli as manage_cli

for cli, arg in (
    (
        config_cli,
        "--help",
    ),
    (config_cli, "-h"),
    (manage_cli, "-h"),
):
    try:
        cli(arg)
        msg = "should have exited"
        raise AssertionError(msg)
    except SystemExit:
        pass
