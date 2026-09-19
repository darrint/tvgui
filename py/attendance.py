import sqlite3
import time

from member import Member, Role


IN = "in"
OUT = "out"


class Punch:
    def __init__(self, member, direction):
        self.member = member
        self.direction = direction


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS people (
                username TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                pronounce TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student'
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS punches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ts INTEGER NOT NULL,
                direction TEXT NOT NULL
            )"""
        )
        try:
            self.conn.execute(
                "ALTER TABLE people ADD COLUMN role TEXT NOT NULL DEFAULT 'student'"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def upsert(self, member):
        self.conn.execute(
            """INSERT INTO people(username, name, pronounce, role) VALUES (?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET
                 name=excluded.name, pronounce=excluded.pronounce, role=excluded.role""",
            (member.username, member.name, member.pronounce, member.role),
        )
        self.conn.commit()

    def toggle(self, member, now_secs, debounce_secs=2):
        self.upsert(member)
        row = self.conn.execute(
            "SELECT ts, direction FROM punches WHERE username=? ORDER BY id DESC LIMIT 1",
            (member.username,),
        ).fetchone()
        if row and now_secs - row[0] < debounce_secs:
            return None
        direction = OUT if row and row[1] == IN else IN
        self.conn.execute(
            "INSERT INTO punches(username, ts, direction) VALUES (?, ?, ?)",
            (member.username, now_secs, direction),
        )
        self.conn.commit()
        return Punch(member, direction)

    def people(self):
        rows = self.conn.execute(
            """SELECT username, name, pronounce, role FROM people
               ORDER BY name COLLATE NOCASE"""
        ).fetchall()
        out = []
        for username, name, pronounce, role in rows:
            m = Member.new(name, username, pronounce, Role.parse(role) or Role.STUDENT)
            if m:
                out.append(m)
        return out

    def who(self):
        rows = self.conn.execute(
            """SELECT p.username, p.name, p.pronounce, p.role, x.ts
                FROM people p
                JOIN (
                  SELECT username, MAX(id) AS id FROM punches GROUP BY username
                ) last ON last.username = p.username
                JOIN punches x ON x.id = last.id
                WHERE x.direction = 'in'
                ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
        out = []
        for username, name, pronounce, role, ts in rows:
            m = Member.new(name, username, pronounce, Role.parse(role) or Role.STUDENT)
            if m:
                out.append((m, ts))
        return out


def now_secs():
    return int(time.time())
