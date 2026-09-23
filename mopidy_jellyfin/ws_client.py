# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

#################################################################################################

import json
import logging
import time
import threading
import mopidy_jellyfin
from .http import JellyfinHttpClient
from .utils import create_headers

import websocket


##################################################################################################

logger = logging.getLogger(__name__)

##################################################################################################


class WSClient(threading.Thread):

    wsc = None
    stop = False

    def __init__(self, client):
        # Start in a second thread to not interfere with the main program

        logger.debug("WSClient initializing...")

        self.client = client
        self.device_id = mopidy_jellyfin.Extension.device_id
        # Load things from config file
        cert = None
        client_cert = self.client.config['jellyfin'].get('client_cert', None)
        client_key = self.client.config['jellyfin'].get('client_key', None)
        if client_cert is not None and client_key is not None:
            cert = (client_cert, client_key)
        self.hostname = self.client.config['jellyfin'].get('hostname')
        proxy = self.client.config.get('proxy', None)

        self.token = self.client.token
        self.headers = create_headers(
            mopidy_jellyfin.Extension.device_name,
            mopidy_jellyfin.Extension.device_id,
            mopidy_jellyfin.__version__,
            self.token
        )

        self.http = JellyfinHttpClient(self.headers, cert, proxy)
        self.retry_count = 0
        self.keepalive_stop = None
        threading.Thread.__init__(self)

    def send(self, message, data=""):
        # Send message to the Jellyfin server

        if self.wsc is None:
            raise ValueError("The websocket client is not started.")

        self.wsc.send(json.dumps({'MessageType': message, "Data": data}))

    def run(self):
        # Starts the websocket event listener

        response_url = self.http.check_redirect(self.hostname)
        if self.hostname != response_url:
            self.hostname = response_url
        if self.hostname.startswith('https'):
            server = self.hostname.replace('https', "wss")
        else:
            server = self.hostname.replace('http', "ws")
        wsc_url = f"{server}/socket"

        self.wsc = websocket.WebSocketApp(
            wsc_url,
            header=self.headers,
            on_message=lambda ws, message: self.on_message(ws, message),
            on_error=lambda ws, error: self.on_error(ws, error),
            on_close=lambda ws, code, reason: self.on_close(ws, code, reason))
        self.wsc.on_open = lambda ws: self.on_open(ws)

        while not self.stop:

            time.sleep(self.retry_count * 5)
            self.wsc.run_forever(ping_interval=10)

            # If connection fails, attempt to reconnect every 60 seconds at max
            max_tries = 12
            if self.retry_count < max_tries:
                self.retry_count += 1

    def on_error(self, ws, error):
        logger.error(error)

    def on_open(self, ws):
        # A working connection starts the backoff over
        self.retry_count = 0
        logger.info('Websocket connected')

        self.post_capabilities()
        self.callback('WebSocketConnect', None)

    def on_close(self, ws, code, reason):
        self.stop_keepalive()
        logger.warning(
            'Websocket closed: code=%s reason=%r', code, reason)

    def on_message(self, ws, message):
        # Receive messages from Jellyfin, sends to callback for processing

        message = json.loads(message)
        data = message.get('Data', {})

        if message['MessageType'] == 'ForceKeepAlive':
            self.start_keepalive(data)
            return

        self.callback(message['MessageType'], data)

    def stop_client(self):
        # Stop the client websocket thread

        self.stop = True

        if self.wsc is not None:
            self.wsc.close()

    def post_capabilities(self):
        # Tell the server what media and controls we can handle

        data = {
            'PlayableMediaTypes': "Audio",
            'SupportsMediaControl': True,
            'SupportedCommands': (
                    "VolumeUp,VolumeDown,ToggleMute,"
                    "SetAudioStreamIndex,"
                    "SetRepeatMode,"
                    "Mute,Unmute,SetVolume,"
                    "Play,Playstate,PlayNext,PlayMediaSource"
            )
        }

        url = '{}/Sessions/Capabilities/Full'.format(self.hostname)

        self.http.post(url, data)

    def callback(self, message, data):
        # Processes events from Jellyfin and sends them to EventListener

        if message == 'Pause':
           self.client.playback.pause_track()
        elif message == 'Play':
            self.client.play_tracks(data)
        elif message == 'Playstate':
            self.client.playstate(data)
        elif message == 'GeneralCommand':
            self.client.general_command(data)

    def start_keepalive(self, timeout):
        # The server sends ForceKeepAlive with its timeout in seconds and
        # drops the connection if no KeepAlive arrives in time; websocket
        # pings are not enough.  Same contract as jellyfin-sdk-typescript:
        # answer right away, then every timeout / 2 seconds, and stop when
        # the socket closes.
        self.stop_keepalive()
        stop = threading.Event()
        self.keepalive_stop = stop
        keepalive = threading.Thread(
            target=self.keepalive_loop, args=(stop, timeout / 2),
            name='jellyfin-keepalive')
        keepalive.daemon = True
        keepalive.start()

    def keepalive_loop(self, stop, interval):
        while not stop.is_set():
            try:
                self.send('KeepAlive')
            except websocket.WebSocketConnectionClosedException as error:
                logger.warning('KeepAlive on a closed websocket: %s', error)
                return
            stop.wait(interval)

    def stop_keepalive(self):
        if self.keepalive_stop is not None:
            self.keepalive_stop.set()
