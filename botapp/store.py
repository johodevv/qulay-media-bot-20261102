"""Private user counters, owner-controlled admin roles, and resumable quiz attempts."""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from botapp.media import MediaError


class BotStore:
    def __init__(self, path: Path, owner_id: int):
        self.path, self.owner_id = path, owner_id
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL, updates INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen_updates(id INTEGER PRIMARY KEY, created REAL);
                CREATE TABLE IF NOT EXISTS admins(
                    id INTEGER PRIMARY KEY, added_by INTEGER NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quizzes(
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts(
                    user_id INTEGER PRIMARY KEY, quiz_id TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0,
                    answers TEXT NOT NULL DEFAULT '[]', phase TEXT NOT NULL,
                    FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS users_last_seen ON users(last_seen DESC);
                CREATE INDEX IF NOT EXISTS quiz_expiration ON quizzes(expires);
                CREATE TABLE IF NOT EXISTS gemini_jobs(
                    user_id INTEGER NOT NULL, started REAL NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES('started', ?)", (str(time.time()),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def touch(self, user, update_id: int) -> None:
        now = time.time()
        name = str(getattr(user, "full_name", "") or getattr(user, "first_name", ""))[:200]
        username = str(getattr(user, "username", "") or "")[:64]
        with self.db() as db:
            if not db.execute(
                "INSERT OR IGNORE INTO seen_updates VALUES(?, ?)", (update_id, now)
            ).rowcount:
                return
            db.execute(
                """INSERT INTO users VALUES(?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, username=excluded.username, last_seen=excluded.last_seen,
                   updates=users.updates+1""",
                (user.id, name, username, now, now),
            )
            db.execute("DELETE FROM seen_updates WHERE created < ?", (now - 172800,))
            db.execute("DELETE FROM quizzes WHERE expires < ?", (now,))

    def is_admin(self, user_id: int) -> bool:
        if self.owner_id and user_id == self.owner_id:
            return True
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM admins WHERE id=?", (user_id,)).fetchone())

    def resolve_user(self, value: str) -> int:
        if value.isascii() and value.isdecimal() and len(value) <= 16:
            user_id = int(value)
            if 0 < user_id < 2**52:
                return user_id
        if not (len(value) > 1 and value.startswith("@")):
            raise MediaError("To'g'ri Telegram ID yoki @username yuboring.")
        with self.db() as db:
            matches = db.execute(
                "SELECT id FROM users WHERE lower(username)=?", (value[1:].lower(),)
            ).fetchall()
        if len(matches) > 1:
            raise MediaError("Username bir nechta yozuvda bor. Telegram ID raqamini yuboring.")
        if not matches:
            raise MediaError(
                "Username ro'yxatda topilmadi. Uning raqamli Telegram ID'sini yuboring."
            )
        return matches[0]["id"]

    def change_admin(self, actor: int, target: int, *, add: bool) -> None:
        if not self.owner_id or actor != self.owner_id:
            raise MediaError("Adminlarni faqat bosh admin boshqaradi.")
        if type(target) is not int or not 0 < target < 2**52:
            raise MediaError("Telegram ID noto'g'ri.")
        if target == self.owner_id:
            raise MediaError("Bosh admin huquqi doim saqlanadi.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if add:
                if db.execute("SELECT count(*) FROM admins").fetchone()[0] >= 50:
                    raise MediaError("Ko'pi bilan 50 ta qo'shimcha admin bo'lishi mumkin.")
                db.execute(
                    "INSERT OR IGNORE INTO admins VALUES(?,?,?)", (target, actor, time.time())
                )
            else:
                db.execute("DELETE FROM admins WHERE id=?", (target,))

    def statistics(self) -> dict:
        now = time.time()
        # Asia/Tashkent is UTC+5 and does not use daylight saving.
        today = (int((now + 18000) // 86400) * 86400) - 18000
        with self.db() as db:
            result = dict(
                db.execute(
                    """SELECT count(*) total, coalesce(sum(last_seen>=?),0) today,
                   coalesce(sum(last_seen>=?),0) week, coalesce(sum(last_seen>=?),0) month,
                   coalesce(sum(updates),0) updates FROM users""",
                    (today, now - 7 * 86400, now - 30 * 86400),
                ).fetchone()
            )
            result["started"] = float(
                db.execute("SELECT value FROM meta WHERE key='started'").fetchone()[0]
            )
            result["admins"] = db.execute("SELECT count(*) FROM admins").fetchone()[0] + bool(
                self.owner_id
            )
        return result

    def users(self, page: int = 0) -> list[dict]:
        page = min(max(0, page), 100000)
        with self.db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM users ORDER BY last_seen DESC, id LIMIT 10 OFFSET ?",
                    (page * 10,),
                )
            ]

    def admins(self) -> list[int]:
        with self.db() as db:
            return ([self.owner_id] if self.owner_id else []) + [
                row[0]
                for row in db.execute("SELECT id FROM admins ORDER BY created")
                if row[0] != self.owner_id
            ]

    def export_metadata(self, actor: int) -> bytes:
        if actor != self.owner_id or not self.owner_id:
            raise MediaError("Zaxirani faqat bosh admin oladi.")
        with self.db() as db:
            payload = {
                "format": "qulay-users-v1",
                "started": self.statistics()["started"],
                "users": [dict(row) for row in db.execute("SELECT * FROM users")],
                "admins": self.admins(),
            }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def restore_metadata(self, actor: int, data: bytes) -> int:
        if actor != self.owner_id or not self.owner_id:
            raise MediaError("Zaxirani faqat bosh admin tiklaydi.")
        try:
            if len(data) > 10 * 1024 * 1024:
                raise ValueError
            payload = json.loads(data)
            if payload["format"] != "qulay-users-v1":
                raise ValueError
            users, admins = payload["users"], payload["admins"]
            if not isinstance(users, list) or len(users) > 50000:
                raise ValueError
            ids = set()
            now = time.time()
            for row in users:
                if type(row["id"]) is not int or not 0 < row["id"] < 2**52:
                    raise ValueError
                if row["id"] in ids:
                    raise ValueError
                ids.add(row["id"])
                if not isinstance(row["name"], str) or len(row["name"]) > 200:
                    raise ValueError
                if not isinstance(row["username"], str) or len(row["username"]) > 64:
                    raise ValueError
                if type(row["updates"]) is not int or not 1 <= row["updates"] <= 10**12:
                    raise ValueError
                for key in ("first_seen", "last_seen"):
                    value = row[key]
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError
                    if not 0 <= value <= now + 86400:
                        raise ValueError
                if row["first_seen"] > row["last_seen"]:
                    raise ValueError
            if not isinstance(admins, list) or len(admins) > 51:
                raise ValueError
            if any(type(value) is not int or not 0 < value < 2**52 for value in admins):
                raise ValueError
            started = float(payload["started"])
            if not math.isfinite(started) or not 0 <= started <= now + 86400:
                raise ValueError
        except (ValueError, KeyError, TypeError, OverflowError, UnicodeError) as exc:
            raise MediaError("Zaxira fayli noto'g'ri. Bot bergan JSON faylni yuboring.") from exc
        with self.db() as db:
            for row in users:
                db.execute(
                    """INSERT INTO users VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                       first_seen=min(users.first_seen,excluded.first_seen),
                       last_seen=max(users.last_seen,excluded.last_seen),
                       updates=max(users.updates,excluded.updates)""",
                    tuple(
                        row[key]
                        for key in ("id", "name", "username", "first_seen", "last_seen", "updates")
                    ),
                )
            for target in admins:
                if target != self.owner_id:
                    db.execute("INSERT OR IGNORE INTO admins VALUES(?,?,?)", (target, actor, now))
            if db.execute("SELECT count(*) FROM admins").fetchone()[0] > 50:
                raise MediaError("Jami 50 tadan ortiq admin chiqmoqda. Tiklash bekor qilindi.")
            db.execute(
                "UPDATE meta SET value=min(cast(value AS REAL),?) WHERE key='started'", (started,)
            )
        return len(users)

    def reserve_gemini_job(self, user_id: int, limit: int) -> None:
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM gemini_jobs WHERE started < ?", (now - 86400,))
            if db.execute("SELECT count(*) FROM gemini_jobs").fetchone()[0] >= limit:
                raise MediaError("Botning AI sinov limiti to'ldi. Oddiy test rejimini ishlating.")
            if db.execute(
                "SELECT 1 FROM gemini_jobs WHERE user_id=? AND started>?", (user_id, now - 60)
            ).fetchone():
                raise MediaError("Yana AI test yuborishdan oldin bir daqiqa kuting.")
            db.execute("INSERT INTO gemini_jobs VALUES(?,?)", (user_id, now))

    def save_quiz(self, user_id: int, questions: list[dict], scanned: bool) -> str:
        quiz_id = secrets.token_hex(6)
        with self.db() as db:
            db.execute("DELETE FROM quizzes WHERE expires < ?", (time.time(),))
            # Keep at most ten source quizzes per user, regardless of repeated uploads.
            db.execute(
                """DELETE FROM quizzes WHERE id IN (SELECT id FROM quizzes WHERE user_id=?
                   ORDER BY expires DESC LIMIT -1 OFFSET 9)""",
                (user_id,),
            )
            db.execute(
                "INSERT INTO quizzes VALUES(?,?,?,?)",
                (
                    quiz_id,
                    user_id,
                    time.time() + 86400,
                    json.dumps({"questions": questions, "scanned": scanned}, ensure_ascii=False),
                ),
            )
        return quiz_id

    @staticmethod
    def _quiz(db, user_id: int, quiz_id: str) -> dict:
        row = db.execute(
            "SELECT payload FROM quizzes WHERE id=? AND user_id=? AND expires>?",
            (quiz_id, user_id, time.time()),
        ).fetchone()
        if row is None:
            raise MediaError("Test eskirgan yoki sizga tegishli emas. Faylni qayta yuboring.")
        return json.loads(row[0])

    def quiz(self, user_id: int, quiz_id: str) -> dict:
        with self.db() as db:
            return self._quiz(db, user_id, quiz_id)

    def start_attempt(self, user_id: int, quiz_id: str) -> dict:
        with self.db() as db:
            self._quiz(db, user_id, quiz_id)
            existing = db.execute("SELECT * FROM attempts WHERE user_id=?", (user_id,)).fetchone()
            if existing and existing["quiz_id"] == quiz_id and existing["phase"] != "done":
                return dict(existing)
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES(?,?,0,0,'[]','question')",
                (user_id, quiz_id),
            )
        return self.attempt(user_id, quiz_id)

    def attempt(self, user_id: int, quiz_id: str) -> dict:
        with self.db() as db:
            self._quiz(db, user_id, quiz_id)
            row = db.execute(
                "SELECT * FROM attempts WHERE user_id=? AND quiz_id=?", (user_id, quiz_id)
            ).fetchone()
        if row is None:
            raise MediaError("Avval «Testni boshlash»ni bosing.")
        return dict(row)

    def reveal_answer(self, user_id: int, quiz_id: str, position: int) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            quiz = self._quiz(db, user_id, quiz_id)
            if not 0 <= position < len(quiz["questions"]):
                raise MediaError("Savol topilmadi.")
            if "model_answer" not in quiz["questions"][position]:
                raise MediaError("Bu savolda javob variantini tanlang.")
            row = db.execute(
                "SELECT phase FROM attempts WHERE user_id=? AND quiz_id=? AND position=?",
                (user_id, quiz_id, position),
            ).fetchone()
            if not row or row["phase"] not in {"question", "review"}:
                raise MediaError("Bu tugma eskirgan.")
            db.execute(
                "UPDATE attempts SET phase='review' WHERE user_id=? AND quiz_id=? AND position=?",
                (user_id, quiz_id, position),
            )

    def answer(self, user_id: int, quiz_id: str, position: int, choice: int) -> dict:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            quiz = self._quiz(db, user_id, quiz_id)
            if not 0 <= position < len(quiz["questions"]):
                raise MediaError("Savol topilmadi.")
            question = quiz["questions"][position]
            open_answer = "model_answer" in question
            row = db.execute("SELECT * FROM attempts WHERE user_id=?", (user_id,)).fetchone()
            if (
                not row
                or row["quiz_id"] != quiz_id
                or row["position"] != position
                or row["phase"] != ("review" if open_answer else "question")
            ):
                raise MediaError("Bu savolga javob berilgan yoki tugma eskirgan.")
            if open_answer:
                if choice not in {0, 1}:
                    raise MediaError("Bahoni tugmadan tanlang.")
                correct = choice
                next_position = position + 1
                phase = "done" if next_position == len(quiz["questions"]) else "question"
            else:
                if not 0 <= choice < len(question["options"]):
                    raise MediaError("Javob varianti noto'g'ri.")
                correct = int(choice == question["correct"])
                next_position, phase = position, "feedback"
            answers = json.loads(row["answers"]) + [choice]
            db.execute(
                "UPDATE attempts SET answers=?, score=?, position=?, phase=? WHERE user_id=?",
                (json.dumps(answers), row["score"] + correct, next_position, phase, user_id),
            )
        return self.attempt(user_id, quiz_id)

    def next_question(self, user_id: int, quiz_id: str, position: int) -> dict:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            quiz = self._quiz(db, user_id, quiz_id)
            row = db.execute("SELECT * FROM attempts WHERE user_id=?", (user_id,)).fetchone()
            if (
                not row
                or row["quiz_id"] != quiz_id
                or row["position"] != position
                or row["phase"] != "feedback"
            ):
                raise MediaError("Bu tugma eskirgan.")
            next_pos = position + 1
            phase = "done" if next_pos >= len(quiz["questions"]) else "question"
            db.execute(
                "UPDATE attempts SET position=?, phase=? WHERE user_id=?",
                (next_pos, phase, user_id),
            )
        return self.attempt(user_id, quiz_id)

    def cancel_attempt(self, user_id: int) -> None:
        with self.db() as db:
            db.execute("DELETE FROM attempts WHERE user_id=?", (user_id,))
