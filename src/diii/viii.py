import asyncio
import logging
import time
import os
import websockets
import datetime
import socket

import serial
import serial.tools.list_ports

from diii.exceptions import DeviceNotFoundError
from diii.websocketserial import WebSocketSerialThreaded

logger = logging.getLogger(__name__)

class Deviceviii:
    def __init__(self, serial=None):
        self.serial = None
        self.is_connected = False
        self.event_handlers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc, exc_type, traceback):
        if self.is_connected:
            self.disconnect()

    def connect(self):
        # self.serial = WebSocketSerialThreaded('localhost', 8765)
        self.serial = WebSocketSerialThreaded("ws://localhost:8765", timeout=0.5)
        self.serial.open()
        logger.info(f'connected to device on {self.serial.port}')

    def disconnect(self):
        self.is_connected = False
        if self.serial is not None:
            self.serial.close()

    def raise_event(self, event, *args, **kwargs):
        try:
            handlers = self.event_handlers[event]
        except KeyError:
            pass
        else:
            for handler in handlers:
                try:
                    handler(*args, **kwargs)
                except Exception as exc:
                    logger.error(f'error in command handler "{event}" ({handler}): {exc}')

    def replace_handlers(self, handlers):
        self.event_handlers = handlers
        # if self.serial is not None:
            # self.serial.event_handlers = handlers

    def reconnect(self, err_event=False):
        try:
            self.connect()
            if self.serial is not None and self.serial.is_open():
                self.is_connected = True
                self.raise_event('connect')
        except Exception as exc:
            if self.is_connected or err_event:
                self.is_connected = False
                self.raise_event('connect_err', exc)

    def writebin(self, b):
        if self.serial is not None:
            if len(b) % 64 == 0:
                b += b'\n'
            logger.debug(f'-> {b}')
            self.serial.write(b)

    def write(self, s):
        self.writebin(s.encode('utf-8'))

    def writeline(self, line):
        self.write(line + '\r\n')

    def writefile(self, fname):
        with open(fname) as f:
            logger.info(f'opened file: {f}')
            for line in f.readlines():
                self.writeline(line.rstrip())
                time.sleep(0.001)

    def _upload(self, fname, event, end):
        self.raise_event(event, fname)
        self.writeline('^^s')
        time.sleep(0.1)
        self.writefile(fname)
        time.sleep(0.1)
        self.writeline(end)
        time.sleep(0.1)
        self.writeline('^^z')

    def execute(self, fname):
        self._upload(fname, 'running', '^^e')

    def upload(self, fname):
        self._upload(fname, 'uploading', '^^w')

    def readbin(self, count):
        b = b''
        if self.serial is not None:
            b = self.serial.read(count)
        if len(b) > 0:
            logger.debug(f'<- {b}')
        return b

    def read(self, count):
        return self.readbin(count).decode('utf-8')

    async def read_forever(self):
        while True:
            sleeptime = 0.001
            try:
                r = self.read(10000)
                r = ""
                if len(r) > 0:
                    lines = r.split('\n\r')
                    for line in lines:
                        self.process_line(line)
            except Exception as exc:
                if self.is_connected:
                    logger.error(f'lost connection: {exc}')
                sleeptime = 0.1
                self.reconnect()
            await asyncio.sleep(sleeptime)

    def process_line(self, line):
        if "^^" in line:
            cmds = line.split('^^')
            for cmd in cmds:
                t3 = cmd.rstrip().partition('(')
                if not any(t3):
                    continue
                evt = t3[0]
                args = t3[2].rstrip(')').split(',')
                self.raise_event('iii_event', line, evt, args)
        elif len(line) > 0:
            self.raise_event('iii_output', line)
