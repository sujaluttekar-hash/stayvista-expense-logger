"""
StayVista Expense Logger — Flask + SocketIO Server
Streams live logs from any logger script to the browser UI.
"""

import os
import sys
import subprocess
import threading
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "sv-expense-logger-2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = {
    "inhouse":  "inhouse_logger.py",
    "foodtown": "foodtown_logger.py",
    "clover":   "clover_logger.py",
}

running_process = None
is_running      = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    return jsonify({"running": is_running})


@socketio.on("run_script")
def handle_run(data):
    global running_process, is_running

    if is_running:
        emit("log", {"text": "A script is already running. Wait for it to finish or stop it first.", "type": "warning"})
        return

    key    = data.get("script", "inhouse")
    script = SCRIPTS.get(key)
    if not script:
        emit("log", {"text": f"Unknown script key: {key}", "type": "error"})
        return

    script_path = os.path.join(BASE_DIR, script)
    if not os.path.exists(script_path):
        emit("log", {"text": f"Script file not found: {script}", "type": "error"})
        return

    is_running = True
    emit("status", {"running": True})
    emit("log", {"text": f"Starting {script}...", "type": "info"})

    def stream():
        global running_process, is_running
        try:
            running_process = subprocess.Popen(
                [sys.executable, "-u", script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
            )
            for line in running_process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tag = "dim"
                low = line.lower()
                if "✅" in line or "done" in low or "submitted" in low:
                    tag = "success"
                elif "❌" in line or "failed" in low or "error" in low:
                    tag = "error"
                elif "⚠️" in line or "warning" in low or "skipped" in low:
                    tag = "warning"
                elif "[log]" in low or "starting" in low or "→" in line or "📝" in line:
                    tag = "info"
                socketio.emit("log", {"text": line, "type": tag})

            running_process.wait()
            rc = running_process.returncode
            if rc == 0:
                socketio.emit("log", {"text": "Script finished successfully.", "type": "success"})
            else:
                socketio.emit("log", {"text": f"Script exited with code {rc}.", "type": "error"})

        except Exception as e:
            socketio.emit("log", {"text": f"Server error: {e}", "type": "error"})
        finally:
            is_running = False
            socketio.emit("status", {"running": False})

    threading.Thread(target=stream, daemon=True).start()


@socketio.on("stop_script")
def handle_stop():
    global running_process, is_running
    if running_process and is_running:
        running_process.terminate()
        is_running = False
        emit("log",    {"text": "Script stopped by user.", "type": "warning"})
        emit("status", {"running": False})
    else:
        emit("log", {"text": "No script is currently running.", "type": "dim"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
