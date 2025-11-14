from semantic_release import cli, config
from pathlib import Path

cfg = config.git.load_config(file=Path("release_config.toml"))
cli.main(cfg=cfg, command="publish")