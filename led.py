#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: tabstop=4:softtabstop=4:shiftwidth=4:noexpandtab

# from dotenv import load_dotenv, dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import time
import datetime
from zoneinfo import ZoneInfo
# import math
import traceback
import errno
from pathlib import Path
import signal
import sys

# Imports
# TinkerForge
from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_ambient_light_v3 import BrickletAmbientLightV3
from tinkerforge.bricklet_led_strip_v2 import BrickletLEDStripV2
from tinkerforge.bricklet_air_quality import BrickletAirQuality

# MQTT
import paho.mqtt.client as mqtt
import json

# Astro
from astral import LocationInfo
from astral.sun import sun

# Command line
import getopt

class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=f"{Path( __file__ ).parent.absolute()}/.env")

	# env.DEBUG mode
	DEBUG: bool = False

	# LED Strip active
	LED: bool = True

	# Log file
	LOG: str = "/tmp/led.py.lasterror"
	# location
	LATITUDE: float = 50
	ALTITUDE: float = 8
	CITY: str = "Berlin"
	COUNTRY: str = "Germany"
	TIMEZONE: str = "Europe/Berlin"

	# FIFO
	FIFO: str = "/tmp/led.py"

	# MQTT
	MQTT_ENABLED: bool = False
	MQTT_BROKER: str = "localhost"
	MQTT_PORT: int = 1883
	MQTT_USER: str = ""
	MQTT_PASS: str = ""
	MQTT_TOPIC: str = "raspi/sensors"
	MQTT_SWTOPIC: str = "raspi/led"

	# Sleep cycle duration
	SLEEP: float = 0.3

	# Tinkerforge
	TF_DURATION: int = 50
	TF_NUM_LEDS: int = 50
	TF_HOST: str = "localhost"
	TF_PORT: int = 4223
	TF_HATUID: str = ""
	TF_LEDUID: str = ""
	TF_AIRUID: str = ""
	TF_LUXUID: str = ""

# color based on humidity
# OK = green -> 30 - 60 %
# NOK = red -> <30 %, >60 %
def humidity_ok(humidity):
	if (humidity < 3000):
		# low humidity
		r = 2
		g = 0
		b = 0
		warn = True
	elif (humidity <6000):
		# OK
		r = 0
		g = 2
		b = 0
		warn = False
	else:
		# high humidity
		r = 0
		g = 0
		b = 2
		warn = True
	return r, g, b, warn

# FIFO
def fifo_start():
	try:
		#mode = 0o644
		if not os.path.exists(env.FIFO):
			if env.DEBUG: print(f'Create FIFO {env.FIFO}')
			#os.mkfifo(env.FIFO, mode)
			os.mkfifo(env.FIFO)
	except OSError as oe:
		if oe.errno != errno.EEXIST:
			raise

# LED control
def leds(strip, r, g, b):
	global led_strip
	# bgr mapping
	leds = [b, g, r] * env.TF_NUM_LEDS
	strip.set_led_values(0, leds)

# LED color baed on status
def ledcolors(brightness, humidity, airpressure, sunrise, sunset, localtime):
	# choose color based on humidity
	r, g, b, warn = humidity_ok(humidity)

	if (warn):
		status = '[ON] humidity warning!'
	elif (localtime > sunset): # Nach Sonnensunset
		status = '[ON] night <00:00'
	elif (localtime < sunrise): # before sunrise
		dow = localtime.weekday()
		# half illumination
		# Mo-Fr >00:00
		if (dow < 5) and (localtime.hour > 0):
			if (localtime.hour < 1):
				status = '[ON - 1/4] - weekly'
				r = r / 4
				g = g / 4
				b = b / 4
			else:
				status = '[OFF] - weekly'
		# weekend
		else:
			if (localtime.hour < 2):
				status = '[ON] weekend >00:00'
			else:
				status = '[OFF] weekend >02:00'
				r = 0
				g = 0
				b = 0
	elif (illu < 6): # on when low light
		status = '[ON] low light'
	else: # off in all other cases
		status = '[OFF] - else -'
		r = 0
		g = 0
		b = 0

	# illumination based on measured brightness
	if (brightness > 6.0):
		status = status + ' - more illumination brightness>6 -> x 3'
		r = r * 3
		g = g * 3
		b = b * 3

	log = f'{localtime.year}-{localtime.month:02}-{localtime.day:02} {localtime.hour:02}:{localtime.minute:02}:{localtime.second:02} brightness {brightness} humidity {humidity} airpressure {airpressure} R {r} G {g} B {b} {status}'

	if env.DEBUG: print(log)

	# write to FIFO
	with os.fdopen(os.open(env.FIFO, os.O_RDWR | os.O_NONBLOCK), 'w') as fd:
		fd.write(log + '\n')
		fd.close()

	# LEDs ansteuern
	leds(led_strip, r, g, b)

# Signal handler
class SignalHandler:
	stop = False

	def __init__(self):
		# Ctrl+C
		signal.signal(signal.SIGINT, self.exit_gracefully)

		# Supervisor/process manager signals
		signal.signal(signal.SIGTERM, self.exit_gracefully)
		signal.signal(signal.SIGQUIT, self.exit_gracefully)

	def exit_error(self, *args):
		with os.fdopen(os.open(env.LOG, os.O_RDWR), 'w') as fd:
			fd.write('program abort')
			fd.close()
		self.stop = True

	def exit_gracefully(self, *args):
		if env.DEBUG: print('graceful exit')
		self.stop = True

# Traceback
def handle_exception(exc_type, exc_value, exc_traceback):
	print(f"Exception:\nType: {exc_type}\nValue: {exc_value}\nTraceback: {exc_traceback}\n")
	with open(env.LOG, "a") as f:
		traceback.print_exception(
			exc_type, exc_value, exc_traceback, file=f
		)

def exit_gracefully(self, signum, frame):
	with open(env.LOG, "a") as f:
		f.write(f"Signal received: {signum}\n")
		traceback.print_stack(frame, file=f)
	self.stop = True

sys.excepthook = handle_exception

#region MQTT Callbacks
def on_connect(client, userdata, flags, reason_code, properties):
	if reason_code == 0:
		if env.DEBUG:
			print("MQTT connected")

		# subscribe to topics
		#client.subscribe("raspi/led/set")
	else:
		print(f"MQTT conenction error: {reason_code}")

def on_message(client, userdata, msg):
	topic = msg.topic
	payload = msg.payload.decode("utf-8")
	if env.DEBUG: print(f"Message received: {topic} → {payload}")
	if topic == "raspi/led/set":
		env.LED = payload == "ON"

#endregion

# Main
if __name__ == "__main__":
	global env, EnvironmentError, led_strip

	# Load settings
	print(f"{Path( __file__ ).parent.absolute()}")
	env = Settings()

	# get cmdline parameters
	try:
		opts, args = getopt.getopt(sys.argv[1:],'d',['env.DEBUG'])
	except getopt.GetoptError:
		print('led.py [-d]')
		sys.exit(2)
	for opt, arg in opts:
		if opt in ('-d', '--env.DEBUG'):
			env.DEBUG = True

	# Debug
	if env.DEBUG:
		print("DEBUG mode\n")
		settings_dict = env.model_dump()
		print(f"{'KEY':<20} {'VALUE':<50} {'TYPE'}")
		print("-" * 80)
		for k, v in settings_dict.items():
			print(f"{k:<20} {str(v):<50} {type(v).__name__}")

	# catch STRG-C and signals
	signal_handler = SignalHandler()

	# init
	now = datetime.datetime.now(ZoneInfo(env.TIMEZONE))
	olddow = -1
	sunrise = 8
	sunset = 20
	locationinfo = LocationInfo(env.CITY, env.COUNTRY, env.TIMEZONE, env.LATITUDE, env.ALTITUDE)

	# create FIFO
	fifo_start()

	# Tinkerforge IP connection
	ipcon = IPConnection()

	# Tinkerforge bricklets
	al = BrickletAmbientLightV3(env.TF_LUXUID, ipcon)
	led_strip = BrickletLEDStripV2(env.TF_LEDUID, ipcon)
	aq = BrickletAirQuality(env.TF_AIRUID, ipcon)

	# open Tinkerforge ip connection
	ipcon.connect(env.TF_HOST, env.TF_PORT)

	# connect to MQTT
	if env.MQTT_ENABLED:
		# MQTT callback client
		client = mqtt.Client(
			callback_api_version=mqtt.CallbackAPIVersion.VERSION2
		)

		# MQTT connection handler
		client.on_connect = on_connect

		# MQTT incmoing message handler
		client.on_message = on_message

		# Login to MQTT if user is set
		if (env.MQTT_USER != ""):
			client.username_pw_set(env.MQTT_USER, env.MQTT_PASS)

		# Connect to MQTT
		client.connect(env.MQTT_BROKER, env.MQTT_PORT)

		# Register to raspi/led/set if MQTT_SWTOPIC is set
		if env.MQTT_SWTOPIC != "":
			client.subscribe(env.MQTT_SWTOPIC)

		# Start MQTT loop
		client.loop_start()

	#led_strip.set_chip_type('WS2801')
	led_strip.set_frame_duration(env.TF_DURATION)

	# infinite loop - STRG-C or SIGILL to stop
	try:
		while not signal_handler.stop:
			# time and date
			now = datetime.datetime.now(ZoneInfo(env.TIMEZONE))
			dow = now.weekday()

			# DEBUG
			if env.DEBUG:
				print("New cycle: ", now)

			# calculate sun rise and sun down once a day and on init
			if (dow != olddow):
				olddow = dow
				s = sun(locationinfo.observer, date=now, tzinfo=locationinfo.timezone)
				sunrise = s["sunrise"]
				sunset = s["sunset"]

			# get illumination data
			illu = al.get_illuminance() / 100.0

			# get air data
			iaq_index, iaq_index_accuracy, temperature, humidity, air_pressure = aq.get_all_values()

			# send MQTT data
			if env.MQTT_ENABLED:
				data = {
					"temperature": temperature / 100.0,
					"humidity": humidity / 100.0,
					"iaq_index": (500 - iaq_index)/5,
					# "iaq_index_accuracy": iaq_index_accuracy,
					"air_pressure": air_pressure / 100.0,
					"illumination": illu
				}
				client.publish(env.MQTT_TOPIC, json.dumps(data), retain=True)

			# controll LED strip
			if env.LED:
				# based und environment data
				ledcolors(illu, humidity, air_pressure, sunrise, sunset, now)
			else:
				# disable
				leds(led_strip, 0, 0, 0)
				# write to FIFO
				log = f'{now.year}-{now.month:02}-{now.day:02} {now.hour:02}:{now.minute:02}:{now.second:02} LED strip is currently disabled'
				with os.fdopen(os.open(env.FIFO, os.O_RDWR | os.O_NONBLOCK), 'w') as fd:
					fd.write(log + '\n')
					fd.close()
				# DEBUG
				if env.DEBUG: print(log)

			# sleep
			time.sleep(env.SLEEP)
	except Exception as e:
		err = f'{now.year}-{now.month:02}-{now.day:02} {now.hour:02}:{now.minute:02}:{now.second:02} An error occured:\n{e}\n'
		if env.DEBUG: print(err)
		with open(env.LOG, 'w+') as f:
			f.write(err)
			f.close()

	# close Tinkerforge ip connection
	ipcon.disconnect()

	# close FIFO
	try:
		if env.DEBUG: print(f'Removing FIFO {env.FIFO}')
		os.remove(env.FIFO)
	finally:
		if env.DEBUG: print('END')
