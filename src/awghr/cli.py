import argparse
import sys

from .config import show_config


def cli(*argv: str):

    # Build command-line argument parser
    parser = argparse.ArgumentParser(
        description="yet another high-throughput workflow manager"
    )
    subparsers = parser.add_subparsers(required=True, help="sub-commands")

    # Add show configuration sub-command
    parser_show_config = subparsers.add_parser(
        "config", description="show configuration", help="show configuration"
    )
    parser_show_config.add_argument("--tags", nargs="+", help="assume these tags")
    parser_show_config.add_argument(
        "--batch", type=str, default=None, help="assume this batch system"
    )
    parser_show_config.set_defaults(subcommand=show_config)

    # Parse command line
    args = parser.parse_args(argv if len(argv) > 0 else sys.argv[1:])

    # Execute sub-command
    args.subcommand(args)
