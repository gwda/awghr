from pathlib import Path

from sphinx_pyproject import SphinxConfig

from awghr import __version__

config = SphinxConfig(
    Path(__file__).parents[1] / "pyproject.toml",
    globalns=globals(),
    config_overrides={"version": __version__},
)

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
