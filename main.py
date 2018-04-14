import os
import os.path
import logging
import sys
from ruamel.yaml import YAML

CONFIG_DIR = os.getenv('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'mqt2tradfri.yaml')
config = YAML(typ='safe').load(open(CONFIG_PATH))

from src.light_gateway import LightGateway

logging.basicConfig(
  level=logging.INFO,
  format="[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
)

LightGateway(config)
