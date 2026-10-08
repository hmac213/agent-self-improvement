"""SQLite trajectory store: the central, queryable record of every run.

One database file holds any number of runs. By default it lives next to the
run directories, at `<runs root>/trajectories.db`, so every replicate of an
experiment (and every round of an evolve loop) can be queried together; set
`experiment.trajectory_db` to put it elsewhere (a relative path is resolved
against the runs root).

Tables (all keyed by `run_id`, the run directory's name):
  runs         one row per run: config, timing, stop reason, final scores
  events       every supervisor event, in order (type + JSON payload)
  generations  one row per harness launch: hash, diff, exit code, calls
  tasks        one row per task attempt: prompt, grade, budget used
  llm_calls    every model call seen by the proxy: tokens, cost, latency,
               plus the full request/response JSON (the ground-truth trace)
  tool_calls   every tool_use the model emitted, joined with the
               tool_result the harness sent back on the next call

Writers are the supervisor thread and the proxy's request threads, and
several runs may share one file (parallel replicates, separate processes),
so the database uses WAL mode, a busy timeout and short IMMEDIATE
transactions; each `TrajectoryDB` serialises its own threads with a lock.
Only the standard library is used.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

DB_FILENAME = "trajectories.db"
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    run_dir       TEXT,
    experiment    TEXT,
    affordance    INTEGER,
    model         TEXT,
    upstream      TEXT,
    sandbox       TEXT,
    seed_harness  TEXT,
    inherited     INTEGER,
    config_json   TEXT,
    started_at    REAL,
    ended_at      REAL,
    stop_reason   TEXT,
    tasks_graded  INTEGER,
    mean_score    REAL,
    llm_calls     INTEGER,
    cost_usd      REAL,
    seconds       REAL,
    summary_json  TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    ts         REAL NOT NULL,
    type       TEXT NOT NULL,
    data_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, type);
CREATE TABLE IF NOT EXISTS generations (
    run_id          TEXT NOT NULL,
    generation      INTEGER NOT NULL,
    launched_at     REAL,
    harness_hash    TEXT,
    harness_changed INTEGER,
    changed_by      TEXT,          -- NULL (unchanged) | 'agent' | 'rollback'
    diff_json       TEXT,
    exited_at       REAL,
    exit_code       INTEGER,
    seconds         REAL,
    llm_calls       INTEGER,
    PRIMARY KEY (run_id, generation)
);
CREATE TABLE IF NOT EXISTS tasks (
    run_id         TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    seq            INTEGER,
    prompt         TEXT,
    posted_at      REAL,
    graded_at      REAL,
    reason         TEXT,
    passed         INTEGER,
    total          INTEGER,
    score          REAL,
    llm_calls      INTEGER,
    seconds        REAL,
    failures_json  TEXT,
    error          TEXT,
    PRIMARY KEY (run_id, task_id)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    run_id                 TEXT NOT NULL,
    call_index             INTEGER NOT NULL,
    ts                     REAL,
    generation             INTEGER,
    task_id                TEXT,
    path                   TEXT,
    provider               TEXT,
    requested_model        TEXT,
    model                  TEXT,
    status                 INTEGER,
    stop_reason            TEXT,
    input_tokens           INTEGER,
    output_tokens          INTEGER,
    cache_read_tokens      INTEGER,
    cache_creation_tokens  INTEGER,
    cost_usd               REAL,
    latency_s              REAL,
    request_json           TEXT,
    response_json          TEXT,
    PRIMARY KEY (run_id, call_index)
);
CREATE INDEX IF NOT EXISTS llm_calls_task ON llm_calls (run_id, task_id);
CREATE TABLE IF NOT EXISTS tool_calls (
    run_id       TEXT NOT NULL,
    call_index   INTEGER NOT NULL,  -- the llm_call whose response requested it
    position     INTEGER NOT NULL,  -- index among that response's tool_use blocks
    tool_use_id  TEXT,
    generation   INTEGER,
    task_id      TEXT,
    name         TEXT,
    input_json   TEXT,
    output       TEXT,              -- from the next request's tool_result; NULL if never sent back
    is_error     INTEGER,
    PRIMARY KEY (run_id, call_index, position)
);
CREATE INDEX IF NOT EXISTS tool_calls_use ON tool_calls (run_id, tool_use_id);
"""

CHILD_TABLES = ("events", "generations", "tasks", "llm_calls", "tool_calls")


def _dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v)


def _loads(v: str | None) -> Any:
    return None if v is None else json.loads(v)


def default_path(runs_root: Path, configured: str | None = None) -> Path:
    """Where a run under `runs_root` records its trajectory."""
    if configured:
        p = Path(configured).expanduser()
        return p if p.is_absolute() else runs_root / p
    return runs_root / DB_FILENAME


def path_for_run(run_dir: Path) -> Path:
    """The database a finished run directory recorded into (from its config.json)."""
    configured = None
    cfg_path = run_dir / "config.json"
    if cfg_path.exists():
        try:
            configured = json.loads(cfg_path.read_text()).get("experiment", {}).get("trajectory_db")
        except json.JSONDecodeError:
            pass
    return default_path(run_dir.parent, configured)


class TrajectoryDB:
    def __init__(self, path: str | Path = ":memory:", readonly: bool = False, timeout: float = 30.0):
        self.path = str(path)
        if readonly:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=timeout,
                                        check_same_thread=False, isolation_level=None)
        else:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, timeout=timeout, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.closed = False
        self.conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        if not readonly:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
                self.conn.execute("PRAGMA synchronous = NORMAL")
            with self._lock:
                self.conn.executescript(SCHEMA)  # idempotent; executescript manages its own transaction
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # --- plumbing -----------------------------------------------------------------
    def _tx(self):
        db = self

        class _Tx:
            def __enter__(self):
                db._lock.acquire()
                try:
                    db.conn.execute("BEGIN IMMEDIATE")
                except BaseException:
                    db._lock.release()
                    raise
                return db.conn

            def __exit__(self, exc_type, *_):
                try:
                    db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    db._lock.release()
                return False

        return _Tx()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self.conn.close()

    def __enter__(self) -> "TrajectoryDB":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # --- writes -------------------------------------------------------------------
    def start_run(self, run_id: str, config: dict, run_dir: Path | str | None = None,
                  seed_harness: str | None = None, inherited: bool = False, started_at: float | None = None) -> None:
        """Register a run. A run id that already exists is replaced wholesale."""
        exp, model, sandbox = config.get("experiment", {}), config.get("model", {}), config.get("sandbox", {})
        with self._tx() as c:
            for t in CHILD_TABLES:
                c.execute(f"DELETE FROM {t} WHERE run_id = ?", (run_id,))
            c.execute(
                "INSERT OR REPLACE INTO runs (run_id, run_dir, experiment, affordance, model, upstream, sandbox, "
                "seed_harness, inherited, config_json, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, str(run_dir) if run_dir else None, exp.get("name"), exp.get("affordance"), model.get("name"),
                 model.get("upstream"), sandbox.get("kind"), seed_harness, int(inherited), _dumps(config),
                 started_at or time.time()),
            )

    def finish_run(self, run_id: str, summary: dict, ended_at: float | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE runs SET ended_at=?, stop_reason=?, tasks_graded=?, mean_score=?, llm_calls=?, cost_usd=?, "
                "seconds=?, summary_json=? WHERE run_id=?",
                (ended_at or time.time(), summary.get("stop_reason"), summary.get("tasks_graded"), summary.get("mean_score"),
                 summary.get("llm_calls"), summary.get("cost_usd"), summary.get("seconds"), _dumps(summary), run_id),
            )

    def record_event(self, run_id: str, event: dict) -> None:
        """Store a supervisor event ({"ts", "type", ...}) and update the structured
        tables it implies (generations, tasks)."""
        ts, etype = event.get("ts", time.time()), event["type"]
        data = {k: v for k, v in event.items() if k not in ("ts", "type")}
        with self._tx() as c:
            if etype == "launch":
                changed_by = None
                if data.get("harness_changed"):
                    prev = c.execute(
                        "SELECT type FROM events WHERE run_id=? AND type IN ('launch','rollback') ORDER BY id DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    changed_by = "rollback" if prev and prev["type"] == "rollback" else "agent"
                c.execute(
                    "INSERT OR REPLACE INTO generations (run_id, generation, launched_at, harness_hash, harness_changed, "
                    "changed_by, diff_json) VALUES (?,?,?,?,?,?,?)",
                    (run_id, data["generation"], ts, data.get("harness_hash"), int(bool(data.get("harness_changed"))),
                     changed_by, _dumps(data.get("diff"))),
                )
            elif etype == "exit":
                c.execute(
                    "UPDATE generations SET exited_at=?, exit_code=?, seconds=?, llm_calls=? WHERE run_id=? AND generation=?",
                    (ts, data.get("exit_code"), data.get("seconds"), data.get("llm_calls"), run_id, data["generation"]),
                )
            elif etype == "task_posted":
                c.execute(
                    "INSERT OR REPLACE INTO tasks (run_id, task_id, seq, prompt, posted_at) VALUES (?,?,?,?,?)",
                    (run_id, data["task_id"], data.get("seq"), data.get("prompt"), ts),
                )
            elif etype == "graded":
                c.execute("INSERT OR IGNORE INTO tasks (run_id, task_id) VALUES (?,?)", (run_id, data["task_id"]))
                c.execute(
                    "UPDATE tasks SET graded_at=?, reason=?, passed=?, total=?, score=?, llm_calls=?, seconds=?, "
                    "failures_json=?, error=? WHERE run_id=? AND task_id=?",
                    (ts, data.get("reason"), data.get("passed"), data.get("total"), data.get("score"), data.get("llm_calls"),
                     data.get("seconds"), _dumps(data.get("failures")), data.get("error"), run_id, data["task_id"]),
                )
            c.execute("INSERT INTO events (run_id, ts, type, data_json) VALUES (?,?,?,?)", (run_id, ts, etype, json.dumps(data)))

    def record_llm_call(self, run_id: str, rec: dict) -> None:
        """Store one proxy log record (see proxy.py) and its tool calls/results."""
        if rec.get("call_index") is None:
            return
        req, resp = rec.get("request") or {}, rec.get("response") or {}
        usage = rec.get("usage") or {}
        idx, gen, task = rec["call_index"], rec.get("generation"), rec.get("task_id")
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, idx, rec.get("ts"), gen, task, rec.get("path"), rec.get("provider"), rec.get("requested_model"),
                 resp.get("model") or req.get("model"), rec.get("status"), resp.get("stop_reason"),
                 usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cache_read_input_tokens"),
                 usage.get("cache_creation_input_tokens"), rec.get("cost_usd"), rec.get("latency_s"),
                 json.dumps(req), json.dumps(resp)),
            )
            # Results of earlier tool calls arrive in the trailing user turn(s) of this request.
            for msg in reversed(req.get("messages") or []):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    break
                for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                        out = b.get("content")
                        c.execute(
                            "UPDATE tool_calls SET output=?, is_error=? WHERE run_id=? AND tool_use_id=? AND output IS NULL",
                            (out if isinstance(out, str) else json.dumps(out), int(bool(b.get("is_error"))),
                             run_id, b["tool_use_id"]),
                        )
            uses = [b for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
            for pos, b in enumerate(uses):
                c.execute(
                    "INSERT OR REPLACE INTO tool_calls (run_id, call_index, position, tool_use_id, generation, task_id, "
                    "name, input_json) VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, idx, pos, b.get("id"), gen, task, b.get("name"), json.dumps(b.get("input", {}))),
                )

    def recorder(self, run_id: str):
        """A callback for LLMProxy(recorder=...) that records into this run.
        Calls that land after the store is closed (a straggling proxy thread) are dropped."""
        return lambda rec: None if self.closed else self.record_llm_call(run_id, rec)

    def import_run_dir(self, run_dir: Path, run_id: str | None = None) -> str:
        """Load a run directory's JSONL logs (events.jsonl, llm_calls.jsonl,
        config.json, summary.json); used for runs recorded before this store."""
        run_id = run_id or run_dir.name

        def jsonl(name: str) -> Iterable[dict]:
            p = run_dir / name
            return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

        cfg = json.loads((run_dir / "config.json").read_text()) if (run_dir / "config.json").exists() else {}
        events = list(jsonl("events.jsonl"))
        start = next((e for e in events if e["type"] == "run_start"), {})
        self.start_run(run_id, cfg, run_dir, start.get("seed"), bool(start.get("inherited")), start.get("ts"))
        for e in events:
            self.record_event(run_id, e)
        for rec in jsonl("llm_calls.jsonl"):
            self.record_llm_call(run_id, rec)
        if (run_dir / "summary.json").exists():
            end = next((e for e in reversed(events) if e["type"] == "run_end"), {})
            self.finish_run(run_id, json.loads((run_dir / "summary.json").read_text()), end.get("ts"))
        return run_id

    # --- reads --------------------------------------------------------------------
    def query(self, sql: str, params: Iterable = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def runs(self) -> list[dict]:
        return self.query("SELECT run_id, experiment, affordance, model, stop_reason, tasks_graded, mean_score, "
                          "llm_calls, cost_usd, started_at, ended_at FROM runs ORDER BY started_at, run_id")

    def run(self, run_id: str) -> dict | None:
        rows = self.query("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        r["config"], r["summary"] = _loads(r.pop("config_json")), _loads(r.pop("summary_json"))
        return r

    def events(self, run_id: str, type: str | None = None) -> list[dict]:
        sql, params = "SELECT ts, type, data_json FROM events WHERE run_id = ?", [run_id]
        if type:
            sql, params = sql + " AND type = ?", params + [type]
        return [{"ts": r["ts"], "type": r["type"], **json.loads(r["data_json"])} for r in self.query(sql + " ORDER BY id", params)]

    def generations(self, run_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM generations WHERE run_id = ? ORDER BY generation", (run_id,))
        for r in rows:
            r["diff"] = _loads(r.pop("diff_json"))
            r["harness_changed"] = bool(r["harness_changed"])
        return rows

    def tasks(self, run_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM tasks WHERE run_id = ? ORDER BY seq, posted_at", (run_id,))
        for r in rows:
            r["failures"] = _loads(r.pop("failures_json"))
        return rows

    def llm_calls(self, run_id: str, payload: bool = False) -> list[dict]:
        cols = "*" if payload else ", ".join(
            c for c in self._columns("llm_calls") if c not in ("request_json", "response_json"))
        rows = self.query(f"SELECT {cols} FROM llm_calls WHERE run_id = ? ORDER BY call_index", (run_id,))
        if payload:
            for r in rows:
                r["request"], r["response"] = _loads(r.pop("request_json")), _loads(r.pop("response_json"))
        return rows

    def tool_calls(self, run_id: str, name: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM tool_calls WHERE run_id = ?", [run_id]
        if name:
            sql, params = sql + " AND name = ?", params + [name]
        rows = self.query(sql + " ORDER BY call_index, position", params)
        for r in rows:
            r["input"] = _loads(r.pop("input_json"))
            r["is_error"] = None if r["is_error"] is None else bool(r["is_error"])
        return rows

    def _columns(self, table: str) -> list[str]:
        return [r["name"] for r in self.query(f"PRAGMA table_info({table})")]


def open_for_run(run_dir: Path) -> TrajectoryDB | None:
    """A database containing `run_dir`'s trajectory: the one it recorded into, or,
    for runs recorded before the store existed, an in-memory import of its logs."""
    path = path_for_run(run_dir)
    if path.exists():
        db = TrajectoryDB(path, readonly=True)
        if db.query("SELECT 1 FROM runs WHERE run_id = ?", (run_dir.name,)):
            return db
        db.close()
    if (run_dir / "events.jsonl").exists():
        db = TrajectoryDB(":memory:")
        db.import_run_dir(run_dir)
        return db
    return None


def resolve_db_path(target: Path) -> Path:
    """Accept a database file, a runs root containing one, or a run directory."""
    if target.is_file():
        return target
    if (target / DB_FILENAME).exists():
        return target / DB_FILENAME
    return path_for_run(target)
