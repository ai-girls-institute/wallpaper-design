from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from types import SimpleNamespace
import hmac
import json
import logging
import sys
from collector import Collector, INTERVAL

READ_PATHS = {'/api/v1/snapshot', '/api/v1/audio', '/api/v1/info'}
FILE_ORIGINS = {'null', 'file://'}

def create_server(data_root, port, read_key, page_path, sensor_log):
    ROOT = Path(data_root)
    PAGE_PATH = Path(page_path)
    READ_KEY = read_key
    args = SimpleNamespace(sensor_log=Path(sensor_log))
    collector = Collector(args.sensor_log, ROOT / 'user-settings.json')
    class LocalTelemetryServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            logging.getLogger('mornye').error('Request handler error', exc_info=True)


    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def json_response(self, value, status=200):
            self.respond(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')).encode('utf-8'),
                         'application/json; charset=utf-8', status)

        def respond(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            origin = getattr(self, '_read_origin', None)
            if origin is not None:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                self.json_response({'error': 'Only loopback requests are accepted'}, 403)
                return False
            origin = self.headers.get('Origin')
            key = self.headers.get('X-Wallpaper-Read-Key')
            if key is not None:
                valid = (self.command == 'GET' and urlparse(self.path).path in READ_PATHS
                         and origin in FILE_ORIGINS | {None, 'http://' + expected}
                         and bool(READ_KEY) and key.isascii() and hmac.compare_digest(key, READ_KEY))
                if not valid:
                    self.json_response({'error': 'Invalid wallpaper read request'}, 403)
                    return False
                self._read_origin = origin if origin in FILE_ORIGINS else None
                return True
            if origin and origin != 'http://' + expected:
                self.json_response({'error': 'Only same-origin configuration is accepted'}, 403)
                return False
            return True

        def do_OPTIONS(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            headers = {value.strip().lower() for value in
                       self.headers.get('Access-Control-Request-Headers', '').split(',') if value.strip()}
            if (self.headers.get('Host') != expected or origin not in FILE_ORIGINS
                    or urlparse(self.path).path not in READ_PATHS
                    or self.headers.get('Access-Control-Request-Method') != 'GET'
                    or headers != {'x-wallpaper-read-key'} or not READ_KEY):
                self.json_response({'error': 'Preflight request denied'}, 403)
                return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Methods', 'GET')
            self.send_header('Access-Control-Allow-Headers', 'X-Wallpaper-Read-Key')
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Vary', 'Origin')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            if not self.local_request():
                return
            path = urlparse(self.path).path
            if path == '/api/v1/snapshot':
                self.json_response(collector.read())
            elif path == '/api/v1/audio':
                self.json_response(collector.read_audio())
            elif path == '/api/v1/info':
                from startup import startup_state
                startup = startup_state(ROOT)
                self.json_response({'schemaVersion': 1, 'sampleIntervalMs': round(INTERVAL * 1000),
                    'historySeconds': 60, 'sensorLogPath': args.sensor_log.name,
                    'startup': {'installed': bool(startup.get('installed'))},
                    'audio': {'endpoint': '/api/v1/audio', 'pollIntervalMs': 33,
                              'sampleRateHz': 48000, 'fftSize': 4096, 'spectrumBins': 48}})
            elif path in ('/', '/configure.html', '/index.html'):
                self.respond(PAGE_PATH.read_bytes(), 'text/html; charset=utf-8')
            else:
                self.json_response({'error': 'No such endpoint'}, 404)

        def do_POST(self):
            if not self.local_request():
                return
            if urlparse(self.path).path != '/api/v1/config':
                self.json_response({'error': 'No such endpoint'}, 404)
                return
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                self.json_response({'error': 'JSON is required'}, 415)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict) or set(value) - {
                    'adapter', 'temperatureSensor', 'temperatureSource', 'awccTemperatureSensor'}:
                    raise ValueError('Invalid configuration fields')
                self.json_response(collector.configure(value))
            except (ValueError, UnicodeError) as exc:
                self.json_response({'error': str(exc)}, 400)
            except OSError:
                self.json_response({'error': '配置文件暂时无法写入，原选择已保留，请稍后重试'}, 503)


    try:
        server = LocalTelemetryServer(('127.0.0.1', port), Handler)
    except Exception:
        collector.close()
        raise
    return server, collector
