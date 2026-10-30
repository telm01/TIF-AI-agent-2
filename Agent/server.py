from flask import Flask, request, jsonify
from flask_cors import CORS
import time

app = Flask(__name__)
# The game is opened as a local file:// page, whose origin is "null" -
# allow any origin so the browser's fetch() to localhost:5000 succeeds.
CORS(app)

BUSY_TIMEOUT = 10   # seconds; if the agent crashes while busy, the game stops waiting

state = {
    "type": None,        # "count" or "add"
    "prompt": None,      # e.g. "Хэдэн зураг байна вэ?"
    "hint": None,
    "answer": None,
    "solved": False,
    "mode": None,        # the game's real mode dropdown value
    "level": None,       # the game's real level dropdown value
    "updated_at": 0,
}

# What qwen (through the agent) wants the game to do next.
control = {"mode": None, "level": None, "seq": 0, "busy": False, "busy_since": 0}


@app.route("/update", methods=["POST"])
def update():
    data = request.get_json(force=True, silent=True) or {}
    state["type"] = data.get("type")
    state["prompt"] = data.get("prompt")
    state["hint"] = data.get("hint")
    state["answer"] = data.get("answer")
    state["solved"] = bool(data.get("solved", False))
    state["mode"] = data.get("mode")
    state["level"] = data.get("level")
    state["updated_at"] = time.time()
    return jsonify({"ok": True})


@app.route("/menu", methods=["POST"])
def menu():
    """index.html (the game menu) calls this when it is shown.
    It clears the current problem, so the voice agent has nothing to help with and goes quiet."""
    for k in ("type", "prompt", "hint", "answer", "mode", "level"):
        state[k] = None
    state["solved"] = False
    state["updated_at"] = time.time()
    control["busy"] = False        # a game must never wait on an old "busy" flag
    return jsonify({"ok": True})


@app.route("/current_problem", methods=["GET"])
def current_problem():
    return jsonify(state)


@app.route("/set_mode", methods=["POST"])
def set_mode():
    data = request.get_json(force=True, silent=True) or {}
    if data.get("busy"):
        # The agent says: "I am choosing now, please wait a moment."
        control["busy"] = True
        control["busy_since"] = time.time()
        return jsonify({"ok": True})
    control["mode"] = data.get("mode")
    control["level"] = data.get("level")
    control["seq"] += 1
    control["busy"] = False
    return jsonify({"ok": True})


@app.route("/control", methods=["GET"])
def get_control():
    busy = control["busy"] and (time.time() - control["busy_since"] < BUSY_TIMEOUT)
    return jsonify({"mode": control["mode"], "level": control["level"],
                    "seq": control["seq"], "busy": busy})


if __name__ == "__main__":
    print("[server] Listening on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000)
