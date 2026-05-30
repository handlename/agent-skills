import sys
import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

class FeedbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence HTTP log clutter in the terminal
        return

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        filepath = sys.argv[1]
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(filepath, 'rb') as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/feedback':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            feedback = json.loads(post_data.decode('utf-8'))
            
            # Save feedback to a sibling JSON file of the specification
            filepath = sys.argv[1]
            feedback_path = os.path.splitext(filepath)[0] + '-feedback.json'
            with open(feedback_path, 'w', encoding='utf-8') as f:
                json.dump(feedback, f, ensure_ascii=False, indent=2)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            
            # Flush stdout and terminate
            print("FEEDBACK_RECEIVED")
            sys.stdout.flush()
            
            # Force server shutdown after response
            sys.exit(0)
        else:
            self.send_response(404)
            self.end_headers()

def run(server_class=HTTPServer, handler_class=FeedbackHandler, port=5500):
    server_address = ('127.0.0.1', port)
    httpd = server_class(server_address, handler_class)
    print(f"SERVER_STARTED_ON_PORT_{port}")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python spec-server.py <html_filepath> <port>")
        sys.exit(1)
    port = int(sys.argv[2])
    run(port=port)
