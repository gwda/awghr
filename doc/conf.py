from pathlib import Path

from sphinx_pyproject import SphinxConfig

config = SphinxConfig(Path(__file__).parents[1] / "pyproject.toml", globalns=globals())
