# https://github.com/home-assistant/home-assistant/blob/dev/homeassistant/components/light/tradfri.py#L55
# add update state
# every 1 second get all lights
# for iterate over added lights
# update state
import logging
import os
import os.path
from ruamel.yaml import YAML

CONFIG_DIR = os.getenv('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'mqt2tradfri.yaml')
config = YAML(typ='safe').load(open(CONFIG_PATH))

from pytradfri import Gateway
from pytradfri.api.libcoap_api import APIFactory
from pytradfri.error import PytradfriError, RequestTimeout
from pytradfri.util import load_json, save_json

from pathlib import Path
import uuid
import argparse
import threading
import time
import datetime

api_factory = APIFactory(host=config['host'], psk_id=config['identity'], psk=config['psk'])

api = api_factory.request

gateway = Gateway()

devices_commands = api(gateway.get_devices())

while True:
  try:
    devices = api(devices_commands)
    lights = [dev for dev in devices if dev.has_light_control]

    print(datetime.datetime.now())

    for device in lights:
      print(device.name + " is now " + str(device.light_control.lights[0].state))
    # Print all lights
    print(lights)
  except RequestTimeout as e:
    print("Gateway timeout")
  finally:
    time.sleep(1)
  
# api(lights[0].light_control.set_state(False))
