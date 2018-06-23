import threading
from pytradfri import Gateway, color
from pytradfri.api.libcoap_api import APIFactory
from pytradfri.error import PytradfriError, RequestTimeout
from pytradfri.command import Command
from pytradfri.device import Device
from pytradfri.const import ROOT_DEVICES
from slugify import slugify
from src.mqtt_controller import MqttController

import json
import time
import logging

logger = logging.getLogger('LightGateway')

REFRESH_EVERY = 5 * 60

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

class MyLight:
  def __init__(self, api, device, client):
    logger.info("Found light: {}".format(device.name))
    self.device = device
    self.api = api
    self.observeThread = None
    self.dirty = True
    self.client = client
    self.payloads = []
    self.change(device)

  def push(self):
    topic = state_light_topic(self.device.name)
    payload = json_light_state(self.device)
    logger.info("Publishing to {} with payload {}".format(topic, payload))
    self.client.publish(topic, payload, 1)

  def change(self, device):
    logger.info("Changing light: {}".format(device.name))
    light_topic = set_light_topic(device.name)
    logger.info('Subscribing to {}'.format(light_topic))
    self.client.unsubscribe(light_topic)
    self.client.subscribe(light_topic)
    self.device = device
    self.observe()
    self.push()
    self.dirty = True

  def on_update(self, updated_device):
    self.device = updated_device
    logger.info("Device state updated: {}".format(self.device.name))
    self.dirty = True

  def update(self):
    self.dirty = False
    if self.observeThread is None:
      self.observe()
    self.transfer()
    if self.dirty:
      logger.info("{} is dirty".format(self.device.name))
      self.fetch()
      self.push()

  def observe(self):
    logger.info("Registering observe for light: {}".format(self.device.name))
    self.observeThread = threading.Thread(target=self.worker, daemon=True)
    self.observeThread.start()
    self.dirty = True

  def worker(self):
    def err_callback(err):
      logger.error("On observe callback error")
      logger.error(err, exc_info=True)
      self.observeThread = None
    try:
      logger.info("Started Worker observe for light: {}".format(self.device.name))
      self.api(self.device.observe(self.on_update, err_callback, duration=60))
      logger.info("Worker ended lifecycle for {}".format(self.device.name))
    except Exception as err:
      logger.error("On observe exception")
      logger.error(err, exc_info=True)
    finally:
      self.observeThread = None

  def consume(self, payload):
    self.payloads.append(payload)
    self.dirty = True

  def transfer(self):
    lc = self.device.light_control
    actions = []
    for payload in self.payloads:
      if payload[PAYLOAD_STATE] == STATE_ON:
        keys = {}
        if PAYLOAD_TRANSITION in payload:
          keys['transition_time'] = int(payload[PAYLOAD_TRANSITION]) * 10

        if PAYLOAD_COLOR_TEMP in payload:
          actions.append(lc.set_color_temp(int(payload[PAYLOAD_COLOR_TEMP]), **keys))
        elif PAYLOAD_BRIGHTNESS in payload: # check of this can be one action!
          brightness = int(payload[PAYLOAD_BRIGHTNESS])
          if brightness == 255:
            brightness = 254
          actions.append(lc.set_dimmer(brightness, **keys))
        else:
          actions.append(lc.set_state(True))
      else:
        actions.append(lc.set_state(False))

    if len(actions) > 0:
      try:
        logger.info("There is {} actions to execute for {}".format(len(actions), self.device.name))
        self.api(actions)
        self.payloads = []
        self.dirty = True
      except RequestTimeout as e:
        logger.info("There was timeout for actions, retry in some time")

  def fetch(self):
    try:
      logger.info("Force fetch state for {}".format(self.device.name))
      def process_result(result):
        return Device(result)
      self.device = self.api(Command('get', [ROOT_DEVICES, self.device.id], process_result=process_result))
      time.sleep(0.1)
    except RequestTimeout as e:
      self.dirty = True
      logger.info("There was timeout for actions, retry in some time")

class LightGateway(MqttController):
  def __init__(self, config):
    MqttController.__init__(self, config)
    self.lights = {}
    self.actions = []
    self.config = config
    self.connected = False
    self.configure_api()
    self.timeouts = 0

    self.next_update = time.time() + REFRESH_EVERY

    try:
      while True:
        self.update()
    except KeyboardInterrupt:
      logger.info("Received KeyboardInterrupt")
      pass

  def configure_api(self):
    logger.info("Configuring api!")
    api_factory = APIFactory(host=self.config['host'], psk_id=self.config['identity'], psk=self.config['psk'])
    self.api = api_factory.request
    self.gateway = Gateway()
    self.lights = {}

  def refresh_lights(self):
    try:
      self.devices_commands = self.api(self.gateway.get_devices())
      devices = self.api(self.devices_commands)
      self.lights = {}
      for dev in devices:
        if dev.has_light_control:
          light_name = slugify(dev.name)
          if light_name in self.lights:
            self.lights[light_name].change(dev)
          else:
            self.lights[light_name] = MyLight(self.api, dev, self.client)
        else:
          logger.info("Ignoring device: {}".format(dev.name))
    except RequestTimeout as e:
      logger.info("There was timeout for fetching lights, retry in some time")
      self.next_update = time.time() + 1
      self.timeouts = self.timeouts + 1

  def on_connect(self, client, userdata, flags, rc):
    logger.info('Connected to broker')
    self.connected = True

  def on_message(self, client, userdata, msg):
    logger.info('Received message: {} with payload: {}'.format(msg.topic, msg.payload))
    light_name = extract_light_name(msg.topic)
    light = self.lights[slugify(light_name)]
    payload = json.loads(msg.payload.decode('utf-8'))

    if light is None:
      logger.error("Could not find light with this name: {}".format(light_name))
      return

    light.consume(payload)

  def should_update_lights(self):
    if time.time() - self.next_update >= 0:
      self.next_update = time.time() + REFRESH_EVERY
      return True
    else:
      return False

  def update(self):
    self.client.loop(timeout = 0.5)
    if self.connected:
      if len(self.lights) == 0:
        self.refresh_lights()
      
      for name in self.lights:
        self.lights[name].update()
    time.sleep(0.1)

