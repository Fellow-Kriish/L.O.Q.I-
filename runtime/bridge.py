import asyncio
import hmac
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui_runtime import runtime


async def serve(port, token, origins, ready=None):
    from websockets.asyncio.server import serve as websocket_serve
    from websockets.exceptions import ConnectionClosed

    async def client(socket):
        provided = parse_qs(urlsplit(socket.request.path).query).get('token', [''])[0]
        if not hmac.compare_digest(provided, token):
            await socket.close(1008, 'Authentication required')
            return
        with runtime.lock:
            runtime.clients += 1

        async def publish():
            previous = ''
            while True:
                payload = json.dumps(runtime.snapshot())
                if payload != previous:
                    await socket.send(payload)
                    previous = payload
                await asyncio.sleep(0.06)

        task = asyncio.create_task(publish())
        try:
            async for raw in socket:
                try:
                    message = json.loads(raw)
                    if isinstance(message, dict) and message.get('type') == 'command':
                        await asyncio.to_thread(runtime.command, message)
                except (ValueError, TypeError):
                    await socket.close(1007, 'Invalid command')
                    break
        except ConnectionClosed:
            pass
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with runtime.lock:
                runtime.clients -= 1
                if runtime.clients == 0:
                    runtime.cancel_confirmation()

    async with websocket_serve(client, '127.0.0.1', port, origins=origins, max_size=2048, ping_interval=10, ping_timeout=10):
        runtime.ui_enabled = True
        if ready:
            ready.set()
        try:
            while not runtime.shutdown.is_set():
                await asyncio.sleep(0.2)
        finally:
            runtime.ui_enabled = False
            runtime.cancel_confirmation()


def run_voice():
    os.chdir(Path(__file__).resolve().parents[1])
    sys.argv = ['main.py']
    try:
        import main
        main.main()
    except Exception as error:
        runtime.update(state='unavailable', online=False, microphoneReady=False, error=f'Voice runtime stopped: {type(error).__name__}. Check the Python console.')
        import traceback
        traceback.print_exc()
    finally:
        runtime.cancel_confirmation()
        runtime.update(online=False, microphoneReady=False, state='unavailable')


def start_voice():
    thread = threading.Thread(target=run_voice, name='loqi-voice', daemon=True)
    thread.start()
    return thread


def stop_voice():
    runtime.shutdown.set()
    runtime.command({'action': 'pause'})
    if runtime.wake_listener:
        runtime.wake_listener.stop()


if __name__ == '__main__':
    port = int(os.environ.get('LOQI_UI_PORT', '8765'))
    token = os.environ.get('LOQI_UI_TOKEN') or secrets.token_urlsafe(32)
    origins = os.environ.get('LOQI_UI_ORIGINS', 'http://localhost:8443,http://127.0.0.1:8443').split(',')
    print(f'Runtime URL: ws://127.0.0.1:{port}/?token={token}', flush=True)
    ready = threading.Event()
    failure = []
    def run_server():
        try:
            asyncio.run(serve(port, token, origins, ready))
        except Exception as error:
            failure.append(error)
            ready.set()
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    ready.wait(5)
    if failure or not ready.is_set():
        raise SystemExit('Cannot bind the local runtime bridge. Close other instances or change LOQI_UI_PORT.')
    voice = start_voice()
    try:
        while server_thread.is_alive():
            server_thread.join(.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop_voice()
        server_thread.join(timeout=2)
        voice.join(timeout=3)
