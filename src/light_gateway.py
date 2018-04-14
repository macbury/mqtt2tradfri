from pytradfri import Gateway, color
from pytradfri.api.libcoap_api import APIFactory
from pytradfri.error import PytradfriError, RequestTimeout
from slugify import slugify
from src.mqtt_controller import MqttController

import json
import time
import logging

logger = logging.getLogger('LightGateway')

REFRESH_EVERY = 10

PAYLOAD_STATE = 'state'
PAYLOAD_BRIGHTNESS = 'brightness'
PAYLOAD_COLOR_TEMP = 'color_temp'
PAYLOAD_TRANSITION = 'transition'

STATE_ON = 'ON'
STATE_OFF = 'OFF'

def state_light_topic(light_name):
  return "light/{}".format(slugify(light_name))

def set_light_topic(light_name):
  return "{}/set".format(state_light_topic(light_name))

def extract_light_name(topic):
  return topic.split('/')[1]

def json_light_state(light):
  lc = light.light_control.lights[0]
  payload = dict()
  if lc.state:
    payload[PAYLOAD_STATE] = STATE_ON
    payload[PAYLOAD_BRIGHTNESS] = lc.dimmer
    payload[PAYLOAD_COLOR_TEMP] = lc.color_temp
  else:
    payload[PAYLOAD_STATE] = STATE_OFF

  return json.dumps(payload)

class LightGateway(MqttController):
  def __init__(self, config):
    MqttController.__init__(self, config)
    self.lights = []
    self.actions = []
    self.config = config
    self.configure_api()

    self.next_update = time.time() + 10

    try:
      while True:
        self.loop_forever()
    except KeyboardInterrupt:
      logger.info("Received KeyboardInterrupt")
      pass

  def configure_api(self):
    logger.info("Configuring api!")
    api_factory = APIFactory(host=self.config['host'], psk_id=self.config['identity'], psk=self.config['psk'])
    self.api = api_factory.request
    self.gateway = Gateway()
    self.devices_commands = self.api(self.gateway.get_devices())

  def refresh_lights(self):
    devices = self.api(self.devices_commands)
    self.lights = []
    for dev in devices:
      if dev.has_light_control:
        logger.info("Found light: {}".format(dev.name))
        self.lights.append(dev)
      else:
        logger.info("Found device: {}".format(dev.name))

  def find_light(self, name):
    for light in self.lights:
      if light.name == name or slugify(light.name) == name:
        return light

  def on_connect(self, client, userdata, flags, rc):
    logger.info('Connected to broker')
    self.refresh_lights()
    for light in self.lights:
      logger.info('Subscribing to {}'.format(set_light_topic(light.name)))
      self.client.subscribe(set_light_topic(light.name))

    self.next_update = time.time()

  def on_message(self, client, userdata, msg):
    logger.info('Received message: {} with payload: {}'.format(msg.topic, msg.payload))
    light_name = extract_light_name(msg.topic)
    light = self.find_light(light_name)
    payload = json.loads(msg.payload.decode('utf-8'))

    if light is None:
      logger.error("Could not find light with this name!")
      return

    lc = light.light_control

    if payload[PAYLOAD_STATE] == STATE_ON:
      keys = {}
      if PAYLOAD_TRANSITION in payload:
        keys['transition_time'] = int(payload[PAYLOAD_TRANSITION]) * 10

      if PAYLOAD_COLOR_TEMP in payload:
        self.actions.append(lc.set_color_temp(int(payload[PAYLOAD_COLOR_TEMP]), **keys))
      elif PAYLOAD_BRIGHTNESS in payload: # check of this can be one action!
        brightness = int(payload[PAYLOAD_BRIGHTNESS])
        if brightness == 255:
          brightness = 254
        self.actions.append(lc.set_dimmer(brightness, **keys))
      else:
        self.actions.append(lc.set_state(True))
    else:
      self.actions.append(lc.set_state(False))

  def should_update_lights(self):
    if time.time() - self.next_update >= 0:
      self.next_update = time.time() + REFRESH_EVERY
      return True
    else:
      return False

  def read_light_states(self):
    logger.info("Broadcasting lights")
    for light in self.lights:
      self.push_light_state(light)

  def push_light_state(self, light):
    topic = state_light_topic(light.name)
    payload = json_light_state(light)
    logger.info("Publishing to {} with payload {}".format(topic, payload))
    self.client.publish(topic, payload, 1)

  def process_actions(self):
    if len(self.actions) == 0:
      return
    logger.info("There is {} actions to execute".format(len(self.actions)))
    self.api(self.actions)
    self.actions = []
    self.next_update = time.time() + 0.1

  def loop_forever(self):
    try:
      self.process_actions()
      if self.should_update_lights():
        self.refresh_lights()
        self.read_light_states()
      self.client.loop(timeout=0.01)
    except Exception as e:
      self.configure_api()
      logger.error('Loop exception!')
      logger.error(e, exc_info=True)
