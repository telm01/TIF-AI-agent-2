import os, json, sqlite3, time
from pathlib import Path

DB_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VoiceAgent"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB = DB_DIR / "memory.db"

# dashboard.html reads this file. It is written next to this script.
DATA_JS = Path(__file__).resolve().parent / "memory_data.js"


class Memory:
    def __init__(self, max_turns=6):
        self.db = sqlite3.connect(DB, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS facts(
            key TEXT PRIMARY KEY, value TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS results(
            ts REAL, round_type TEXT, correct INTEGER);
        """)
        self.turns = []          # short-term
        self.max_turns = max_turns
        self.export_js()

    # --- short-term ---
    def add_turn(self, role, text):
        self.turns.append({"role": role, "content": text})
        self.turns = self.turns[-self.max_turns * 2:]

    # --- long-term facts ---
    def remember(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO facts VALUES(?,?,?)",
            (key, value, time.time()))
        self.db.commit()
        self.export_js()

    def facts_text(self):
        rows = self.db.execute("SELECT key,value FROM facts").fetchall()
        return "; ".join(f"{k}: {v}" for k, v in rows)

    # --- progress ---
    def log_result(self, round_type, correct):
        self.db.execute("INSERT INTO results VALUES(?,?,?)",
                        (time.time(), round_type, int(correct)))
        self.db.commit()
        self.export_js()

    def weak_spots(self, n=2):
        rows = self.db.execute("""
            SELECT round_type, AVG(correct) a, COUNT(*) c
            FROM (SELECT * FROM results ORDER BY ts DESC LIMIT 50)
            GROUP BY round_type HAVING c >= 3
            ORDER BY a ASC LIMIT ?""", (n,)).fetchall()
        return [r[0] for r in rows]

    # --- dashboard export ---
    def export_js(self):
        """Write memory_data.js so dashboard.html can show the summary."""
        data = {
            "updated": time.time(),
            "facts": self.db.execute(
                "SELECT key, value FROM facts ORDER BY key").fetchall(),
            "progress": self.db.execute(
                "SELECT round_type, SUM(correct), COUNT(*) FROM results "
                "GROUP BY round_type ORDER BY round_type").fetchall(),
            "recent": self.db.execute(
                "SELECT ts, round_type, correct FROM results "
                "ORDER BY ts DESC LIMIT 15").fetchall(),
            "weak": self.weak_spots(),
        }
        try:
            DATA_JS.write_text(
                "window.MEMORY = " + json.dumps(data) + ";", encoding="utf-8")
        except OSError:
            pass  # never let the dashboard break the agent