import asyncio
import threading
import queue
import websockets
from io import BytesIO
import serial.serialutil  # For SerialException

class WebSocketSerialThreaded:
    """
    A class that mimics the interface of serial.Serial but uses a WebSocket
    connection managed in a separate thread with asyncio. Communication
    with the calling thread is done via synchronized queues.
    """

    def __init__(self, url, baudrate=9600, bytesize=8, parity='N', stopbits=1, timeout=None, xonxoff=False, rtscts=False, dsrdtr=False, **kwargs):
        self.url = url
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.xonxoff = xonxoff
        self.rtscts = rtscts
        self.dsrdtr = dsrdtr
        self._is_open = False
        self._read_queue = queue.Queue()
        self._write_queue = queue.Queue()
        self._event_loop = None
        self._thread = None
        self._stop_event = threading.Event()

    def _run_websocket_loop(self):
        async def websocket_handler():
            try:
                async with websockets.connect(self.url) as websocket:
                    print(f"WebSocketThread: Connected to {self.url}")
                    self._is_open = True

                    async def sender():
                        while not self._stop_event.is_set():
                            try:
                                data_to_send = self._write_queue.get(timeout=0.1)
                                await websocket.send(data_to_send)
                                # print(f"WebSocketThread: Sent {data_to_send}")
                            except queue.Empty:
                                await asyncio.sleep(0.01)
                            except websockets.exceptions.ConnectionClosedOK:
                                print("WebSocketThread: Server closed connection gracefully (send).")
                                break
                            except websockets.exceptions.ConnectionClosedError as e:
                                print(f"WebSocketThread: Server closed connection unexpectedly (send): {e}")
                                break
                            except Exception as e:
                                print(f"WebSocketThread: Error during send: {e}")
                                break

                    async def receiver():
                        while not self._stop_event.is_set():
                            try:
                                received = await websocket.recv()
                                if isinstance(received, str):
                                    self._read_queue.put(received.encode('utf-8'))
                                elif isinstance(received, bytes):
                                    self._read_queue.put(received)
                                # print(f"WebSocketThread: Received {received}")
                            except websockets.exceptions.ConnectionClosedOK:
                                print("WebSocketThread: Server closed connection gracefully (recv).")
                                break
                            except websockets.exceptions.ConnectionClosedError as e:
                                print(f"WebSocketThread: Server closed connection unexpectedly (recv): {e}")
                                break
                            except Exception as e:
                                print(f"WebSocketThread: Error during receive: {e}")
                                break
                            await asyncio.sleep(0.01)

                    await asyncio.gather(sender(), receiver())

            # except websockets.exceptions.ConnectionRefusedError:
                # print(f"WebSocketThread: Connection refused to {self.url}")
            except Exception as e:
                print(f"WebSocketThread: Error in WebSocket loop: {e}")
            finally:
                self._is_open = False
                print(f"WebSocketThread: WebSocket loop finished.")

        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)
        self._event_loop.run_until_complete(websocket_handler())

    def open(self):
        if self._is_open or self._thread:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_websocket_loop, daemon=True)
        self._thread.start()
        # Wait for a short time to allow connection attempt
        import time
        time.sleep(0.1)
        if not self._is_open and not self._stop_event.is_set():
            raise serial.SerialException(f"Failed to open WebSocket connection to {self.url}")

    def is_open(self):
        return self._is_open

    def close(self):
        if self._is_open or self._thread:
            self._stop_event.set()
            if self._event_loop and not self._event_loop.is_closed():
                self._event_loop.call_soon_threadsafe(self._event_loop.stop)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1)
            self._thread = None
            self._is_open = False
            print(f"WebSocketThread: Closed.")

    def write(self, data):
        if not self._is_open:
            raise serial.SerialException("Port is not open")
        if isinstance(data, str):
            data = data.encode('utf-8')
        self._write_queue.put(data)
        return len(data)

    def read(self, size=1):
        if not self._is_open:
            raise serial.SerialException("Port is not open")
        data = b''
        try:
            for _ in range(size):
                byte = self._read_queue.get(timeout=self.timeout)
                data += byte
        except queue.Empty:
            pass
        return data

    def readline(self, size=-1):
        if not self._is_open:
            raise serial.SerialException("Port is not open")
        line = b''
        while True:
            try:
                byte = self._read_queue.get(timeout=self.timeout)
                line += byte
                if byte == b'\n':
                    break
                if size != -1 and len(line) >= size:
                    break
            except queue.Empty:
                break
        return line

    def in_waiting(self):
        return self._read_queue.qsize()

    @property
    def port(self):
        return self.url

    @port.setter
    def port(self, value):
        if self._is_open:
            raise serial.SerialException("Cannot change port while open")
        self.url = value

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

# Example Usage (requires a running WebSocket server):
if __name__ == "__main__":
    import time

    try:
        ws_serial = WebSocketSerialThreaded("ws://localhost:8765", timeout=10000)
        ws_serial.open()

        if ws_serial.is_open():
            ws_serial.write(b"Hello from ThreadedWebSocketSerial!\n")
            print("Main Thread: Sent data.")
            time.sleep(0.5)

            for _ in range(5):
                line = ws_serial.readline()
                if line:
                    print(f"Main Thread: Received line: {line.decode('utf-8').strip()}")
                time.sleep(0.2)

            ws_serial.write(b"REQUEST_RESPONSE\n")
            print("Main Thread: Sent request for response.")
            response = ws_serial.readline()
            if response:
                print(f"Main Thread: Received response: {response.decode('utf-8').strip()}")

            ws_serial.close()

        else:
            print("Main Thread: Failed to open WebSocketSerial.")

    except serial.SerialException as e:
        print(f"Main Thread: Serial exception: {e}")
    except Exception as e:
        print(f"Main Thread: An unexpected error occurred: {e}")
