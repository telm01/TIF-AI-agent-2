
from flask import Flask, request, jsonify
from flask_cors import CORS
import time

app = Flask(__name__)
# The game is opened as a local file:// page, whose origin is "null" —
# allow any origin so the browser's fetch() to localhost:5000 succeeds.
CORS(app)

state = {
    "type": None,       # "count" or "add"
    "prompt": None,      # e.g. "Хэдэн зураг байна вэ?"
    "hint": None,
    "answer": None,
    "solved": False,
    "updated_at": 0,
}


@app.route("/update", methods=["POST"])
def update():
    data = request.get_json(force=True, silent=True) or {}
    state["type"] = data.get("type")
    state["prompt"] = data.get("prompt")
    state["hint"] = data.get("hint")
    state["answer"] = data.get("answer")
    state["solved"] = bool(data.get("solved", False))
    state["updated_at"] = time.time()
    return jsonify({"ok": True})


@app.route("/current_problem", methods=["GET"])
def current_problem():
    return jsonify(state)


if __name__ == "__main__":
    print("[server] Listening on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000)
