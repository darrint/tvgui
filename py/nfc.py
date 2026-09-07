import hashlib
import os
import threading
import time

from attendance import IN, OUT, now_secs
from member import Member, Role
import ndef

LED = [0xFF, 0x00, 0x40, 0x2E, 0x04, 0x05, 0x01, 0x01, 0x00]
DEBOUNCE = 2
FACTORY_PWD = bytes([0xFF, 0xFF, 0xFF, 0xFF])
AUTH0_USER = 0x04
CFG_PAGES = {
    0x0F: (0x29, 0x2A, 0x2B, 0x2C),
    0x11: (0x83, 0x84, 0x85, 0x86),
    0x13: (0xE3, 0xE4, 0xE5, 0xE6),
}


def split_here(who):
    mentors, students = [], []
    for m in who:
        if m.role == Role.MENTOR:
            mentors.append(m.name)
        else:
            students.append(m.name)
    return mentors, students


def _acr_reader():
    from smartcard.System import readers

    for r in readers():
        if "ACR122U" in str(r):
            return r
    return None


def _transmit(conn, apdu):
    data, sw1, sw2 = conn.transmit(list(apdu))
    return bytes(data), sw1, sw2


def pn532(conn, tag_cmd):
    apdu = [0xFF, 0x00, 0x00, 0x00, 2 + 1 + len(tag_cmd), 0xD4, 0x40, 0x01] + list(tag_cmd)
    body, sw1, sw2 = _transmit(conn, apdu)
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"sw {sw1:02X} {sw2:02X}")
    if len(body) >= 3 and body[0] == 0xD5 and body[1] == 0x41:
        if body[2] != 0:
            raise RuntimeError(f"status {body[2]:02X}")
        return body[3:]
    return body


def read_pages(conn, page):
    data = pn532(conn, [0x30, page])
    return data[:16] if len(data) > 16 else data


def write_page(conn, page, four):
    pn532(conn, [0xA2, page] + list(four))
    time.sleep(0.008)


def cfg_pages_for_storage(size_byte):
    return CFG_PAGES.get(size_byte, CFG_PAGES[0x11])


def get_version(conn):
    data = pn532(conn, [0x60])
    return data


def cfg_pages(conn):
    try:
        ver = get_version(conn)
        if len(ver) >= 7:
            return cfg_pages_for_storage(ver[6])
    except Exception:
        pass
    return cfg_pages_for_storage(0x11)


def pwd_auth(conn, pwd):
    data = pn532(conn, [0x1B] + list(pwd))
    return data[:2]


def tag_secret():
    return os.environ.get("TVGUI_TAG_SECRET", "").strip()


def tag_key():
    secret = tag_secret()
    if not secret:
        raise RuntimeError("TVGUI_TAG_SECRET is not set")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def tag_pwd():
    return tag_key()[:4]


def tag_pack():
    return tag_key()[4:6]


def unlock(conn):
    try:
        pwd_auth(conn, tag_pwd())
        return True
    except RuntimeError as e:
        print(f"nfc: {e}", flush=True)
    except Exception:
        pass
    try:
        pwd_auth(conn, FACTORY_PWD)
    except Exception:
        pass
    return False


def protect(conn):
    pwd = tag_pwd()
    pack = tag_pack()
    cfg0, cfg1, pwd_pg, pack_pg = cfg_pages(conn)
    write_page(conn, pwd_pg, pwd)
    write_page(conn, pack_pg, pack + b"\x00\x00")
    try:
        old1 = read_pages(conn, cfg1)
        access = old1[0] if old1 else 0
        access = access & ~0xC0
        rest = old1[1:4] if old1 and len(old1) >= 4 else b"\x00\x00\x00"
        write_page(conn, cfg1, bytes([access]) + rest)
    except Exception:
        write_page(conn, cfg1, bytes([0x00, 0x00, 0x00, 0x00]))
    try:
        old0 = read_pages(conn, cfg0)
        prefix = old0[:3] if old0 and len(old0) >= 3 else b"\x00\x00\x00"
        write_page(conn, cfg0, prefix + bytes([AUTH0_USER]))
    except Exception:
        write_page(conn, cfg0, bytes([0x00, 0x00, 0x00, AUTH0_USER]))


def beep(conn):
    try:
        _transmit(conn, LED)
    except Exception:
        pass


def read_member(conn):
    user = bytearray()
    page = 4
    for _ in range(16):
        chunk = read_pages(conn, page)
        if not chunk:
            break
        user.extend(chunk)
        if 0xFE in chunk:
            break
        page += max(1, len(chunk) // 4)
    return ndef.parse_member_from_user_memory(bytes(user))


def write_member(conn, member):
    unlock(conn)
    pages = ndef.tag_pages(member)
    for i, page in enumerate(pages):
        write_page(conn, 3 + i, page)
    protect(conn)
    unlock(conn)


def connect_reader(reader):
    conn = reader.createConnection()
    try:
        conn.connect()
        return conn
    except Exception:
        return None


def read_member_retry(reader, tries=8):
    for _ in range(tries):
        conn = connect_reader(reader)
        if conn is None:
            time.sleep(0.04)
            continue
        try:
            ours = unlock(conn)
            m = read_member(conn)
            if m:
                if not ours:
                    try:
                        protect(conn)
                    except Exception as e:
                        print(f"protect: {e}", flush=True)
                return conn, m
        except Exception as e:
            print(f"read: {e}", flush=True)
        try:
            conn.disconnect()
        except Exception:
            pass
        time.sleep(0.04)
    return None, None


class EnrollReq:
    def __init__(self, member, reply_q):
        self.member = member
        self.reply_q = reply_q


class EnrollSlot:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    def take(self):
        with self.lock:
            job = self.job
            self.job = None
            return job

    def offer(self, job):
        with self.lock:
            if self.job is not None:
                return False
            self.job = job
            return True

    def clear(self):
        with self.lock:
            self.job = None


def start(store, enroll_slot, event_q):
    t = threading.Thread(target=_run, args=(store, enroll_slot, event_q), daemon=True)
    t.start()
    return t


def _run(store, enroll_slot, event_q):
    active = None
    gone = 0
    hold_status = False
    while True:
        reader = _acr_reader()
        if reader is None:
            event_q.put(("status", "Waiting for reader"))
            time.sleep(1)
            continue
        event_q.put(("status", "Hold your badge over the reader"))
        while _acr_reader() is not None:
            job = enroll_slot.take()
            if job:
                event_q.put(("status", f"Enroll {job.member.name}: tap tag to write"))
                ok = False
                err = "timed out"
                for _ in range(200):
                    conn = connect_reader(reader)
                    if conn:
                        try:
                            write_member(conn, job.member)
                            time.sleep(0.08)
                            back = read_member(conn)
                            if back != job.member:
                                raise RuntimeError("read-back mismatch")
                            beep(conn)
                            event_q.put(("status", f"Enrolled {job.member.name}"))
                            event_q.put(("speak", f"{job.member.pronounce} enrolled"))
                            job.reply_q.put(None)
                            ok = True
                        except Exception as e:
                            err = str(e)
                            print(f"enroll: {e}", flush=True)
                            event_q.put(("status", f"Enroll failed: {e}"))
                            job.reply_q.put(err)
                            ok = True
                        try:
                            conn.disconnect()
                        except Exception:
                            pass
                        hold_status = True
                        break
                    time.sleep(0.25)
                if not ok:
                    print("enroll: timed out", flush=True)
                    event_q.put(("status", "Enroll timed out"))
                    job.reply_q.put(err)
                    hold_status = True
                active = None
                continue

            conn = connect_reader(reader)
            present = conn is not None
            if present:
                gone = 0
                if active is None:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
                    conn, member = read_member_retry(reader)
                    if member:
                        punch = store.toggle(member, now_secs(), DEBOUNCE)
                        if punch:
                            beep(conn)
                            greet = "Welcome" if punch.direction == IN else "good bye"
                            event_q.put(
                                (
                                    "greet",
                                    f"{greet} {punch.member.role}",
                                    punch.member.pronounce,
                                )
                            )
                            verb = "badged in" if punch.direction == IN else "badged out"
                            event_q.put(("status", f"{punch.member.name} {verb}"))
                            event_q.put(("here",) + split_here(store.who()))
                            hold_status = False
                        active = member.username
                        try:
                            conn.disconnect()
                        except Exception:
                            pass
                    else:
                        event_q.put(("status", "Unknown or unreadable tag"))
                        active = ""
                else:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
            else:
                if active is not None:
                    gone += 1
                    if gone >= 3:
                        active = None
                        gone = 0
                        if not hold_status:
                            event_q.put(("status", "Hold your badge over the reader"))
            time.sleep(0.25)
        event_q.put(("status", "Reader unplugged"))
