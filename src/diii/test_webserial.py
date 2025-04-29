import asyncio
import websockets

async def echo(websocket):
    """
    Handles incoming WebSocket connections.  For each connection, it:
    - Prints a message when the client connects.
    - Enters a loop to receive messages from the client.
    - If the message is "REQUEST_RESPONSE", it sends back a specific response.
    - Otherwise, it echoes the received message back to the client.
    - Prints a message when the client disconnects.
    """
    print(f"Server: Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            print(f"Server received: {message}")
            if message == "REQUEST_RESPONSE":
                await websocket.send(f"This is the server's response.\n")
            else:
                await websocket.send(message)  # Echo back the message
    except websockets.ConnectionClosed as e:
        print(f"Server: Client disconnected: {websocket.remote_address}, code={e.code}, reason={e.reason}")
    except Exception as e:
        print(f"Server: Exception during connection with {websocket.remote_address}: {e}")
    finally:
        print(f"Server: Connection with {websocket.remote_address} closed.")

async def main():
    """
    Starts the WebSocket server.
    - Listens on localhost and port 8765.
    - Uses the 'echo' function to handle incoming connections.
    - Runs the server indefinitely using asyncio.Future().
    """
    server_address = "localhost"
    server_port = 8765
    try:  # Add a try-except block here
        async with websockets.serve(echo, server_address, server_port):
            print(f"Server: WebSocket server started at ws://{server_address}:{server_port}")
            await asyncio.Future()  # Run forever
    except Exception as e:
        print(f"Server: Error starting server: {e}") #error message

if __name__ == "__main__":
    """
    Entry point for the script.  Runs the main asyncio function.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server: Received KeyboardInterrupt.  Stopping server...")
    except Exception as e:
        print(f"Server: An error occurred: {e}")
