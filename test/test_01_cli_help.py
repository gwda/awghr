# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

from awghr.cli import cli

for args in (
    ("--help",),
    ("jobs", "-h"),
    ("config", "-h"),
):
    try:
        cli(*args)
        msg = "should have exited"
        raise AssertionError(msg)
    except SystemExit:
        pass
