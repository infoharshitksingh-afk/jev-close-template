"""
The question log: every question the agent gets, what it answered, who it asked, what they said,
and whether it worked. SQLite on disk (safe for several Slack events at once), exported to Excel
on demand.

One row per question in `questions`. Every change is also written to `events`, so the Excel
export can show the full trail: who asked what, when, and what changed.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    ("id", "Question ID"),
    ("opened_at", "Opened (UTC)"),
    ("close_month", "Close month"),
    ("asked_by", "Asked by"),
    ("channel", "Channel"),
    ("thread_link", "Slack thread"),
    ("question", "Question"),
    ("area", "Area"),
    ("problem", "Problem that came up"),
    ("agent_answer", "Agent's answer"),
    ("evidence", "Evidence (query / cell)"),
    ("status", "Status"),
    ("escalated_to", "Asked (owner)"),
    ("escalation_reason", "Why it was escalated"),
    ("escalation_question", "What the owner was asked"),
    ("owner_answer", "Owner's answer"),
    ("answered_by", "Answered by"),
    ("answered_at", "Answered (UTC)"),
    ("decision", "Decision / what changes"),
    ("change_type", "Change type"),
    ("worked", "Worked?"),
    ("worked_note", "Outcome note"),
    ("closed_at", "Closed (UTC)"),
    ("related_to", "Related to"),
]
COLS = [f for f, _ in FIELDS]

STATUSES = ("answered", "waiting_on_owner", "owner_answered", "closed")
CHANGE_TYPES = ("none", "explanation_only", "config_change", "input_cell_change", "source_data_fix",
                "billing_correction", "process_change")
ESCALATION_REASONS = ("judgment_call", "sources_disagree", "contradicts_prior_decision",
                      "unexplained_flag", "would_change_numbers", "data_not_available")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS questions ({", ".join(c + " TEXT" for c in COLS)}, PRIMARY KEY (id));
CREATE TABLE IF NOT EXISTS events (at TEXT, question_id TEXT, actor TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS followups (message_ts TEXT PRIMARY KEY, question_id TEXT, asked_user TEXT);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class QuestionLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as c:
            c.executescript(SCHEMA)

    def _con(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _event(self, c, qid, actor, event, detail=""):
        c.execute("INSERT INTO events VALUES (?,?,?,?,?)", (now(), qid, actor or "", event, detail or ""))

    # ---------------------------------------------------------------- writes
    def open(self, *, question, asked_by, close_month, channel="", thread_link="", area="", problem="",
             related_to=""):
        with self._con() as c:
            n = c.execute("SELECT COUNT(*) FROM questions").fetchone()[0] + 1
            qid = f"Q-{n:04d}"
            row = dict.fromkeys(COLS, "")
            row.update(id=qid, opened_at=now(), close_month=close_month, asked_by=asked_by, channel=channel,
                       thread_link=thread_link, question=question, area=area, problem=problem,
                       status="answered", worked="pending", related_to=related_to)
            c.execute(f"INSERT INTO questions VALUES ({','.join('?' * len(COLS))})", [row[k] for k in COLS])
            self._event(c, qid, asked_by, "asked", question)
        return qid

    def update(self, qid, actor, event, **fields):
        bad = set(fields) - set(COLS)
        if bad:
            raise ValueError(f"unknown fields: {bad}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if fields.get("change_type") and fields["change_type"] not in CHANGE_TYPES:
            raise ValueError(f"change_type must be one of {CHANGE_TYPES}")
        if fields.get("escalation_reason") and fields["escalation_reason"] not in ESCALATION_REASONS:
            raise ValueError(f"escalation_reason must be one of {ESCALATION_REASONS}")
        if fields.get("worked") and fields["worked"] not in ("yes", "no", "pending"):
            raise ValueError("worked must be yes, no or pending")
        with self._con() as c:
            if not c.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
                raise KeyError(f"no question {qid}")
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE questions SET {sets} WHERE id=?", [*fields.values(), qid])
            detail = "; ".join(f"{k}: {v}" for k, v in fields.items() if k not in ("status",) and v)
            self._event(c, qid, actor, event, detail)

    def add_followup(self, message_ts, qid, asked_user):
        with self._con() as c:
            c.execute("INSERT OR REPLACE INTO followups VALUES (?,?,?)", (message_ts, qid, asked_user))

    def followup(self, message_ts):
        with self._con() as c:
            r = c.execute("SELECT * FROM followups WHERE message_ts=?", (message_ts,)).fetchone()
        return dict(r) if r else None

    # ---------------------------------------------------------------- reads
    def get(self, qid):
        with self._con() as c:
            r = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        return dict(r) if r else None

    def all(self):
        with self._con() as c:
            return [dict(r) for r in c.execute("SELECT * FROM questions ORDER BY id")]

    def events(self):
        with self._con() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY rowid")]

    def open_in_thread(self, thread_link):
        with self._con() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM questions WHERE thread_link=? AND status<>'closed' ORDER BY id", (thread_link,))]

    def waiting(self):
        with self._con() as c:
            return [dict(r) for r in c.execute("SELECT * FROM questions WHERE status='waiting_on_owner' ORDER BY id")]

    def search(self, text="", area="", limit=8):
        """Keyword search over past questions, answers and decisions. Newest first."""
        words = [w for w in text.lower().split() if len(w) > 2]
        rows = self.all()
        if area:
            rows = [r for r in rows if r["area"] == area]
        scored = []
        for r in rows:
            blob = " ".join(str(r[k] or "") for k in ("question", "problem", "agent_answer", "owner_answer",
                                                      "decision", "escalation_question")).lower()
            score = sum(blob.count(w) for w in words) if words else 1
            if score:
                scored.append((score, r["id"], r))
        scored.sort(key=lambda x: (-x[0], x[1]), reverse=False)
        keep = ("id", "opened_at", "close_month", "question", "area", "agent_answer", "status", "escalated_to",
                "owner_answer", "answered_by", "decision", "change_type", "worked", "worked_note")
        return [{k: r[k] for k in keep} for _, _, r in scored[:limit]]
