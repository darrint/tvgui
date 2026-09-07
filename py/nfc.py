import hashlib
import os
import threading
import time

from attendance import IN, OUT, now_secs
from member import Member, Role
import ndef

BUZZER_OFF = [0xFF, 0x00, 0x52, 0x00, 0x00]
DEBOUNCE = 2
FACTORY_PWD = bytes([0xFF, 0xFF, 0xFF, 0xFF])
AUTH0_USER = 0x04
CFG_PAGES = {
    0x0F: (0x29, 0x2A, 0x2B, 0x2C),
    0x11: (0x83, 0x84, 0x85, 0x86),
    0x13: (0xE3, 0xE4, 0xE5, 0xE6),
}


def split_here(who):
    mentors, students, parents = [], [], []
    for m in who:
        if m.role == Role.MENTOR:
            mentors.append(m.name)
        elif m.role == Role.PARENT:
            parents.append(m.name)
        else:
            students.append(m.name)
    return mentors, students, parents


def _acr_reader():
    from smartcard.System import readers

    for r in readers():
        if "ACR122U" in str(r):
            return r
    return None


def _transmit(conn, apdu):
    try:
        data, sw1, sw2 = conn.transmit(list(apdu))
    except Exception as e:
        raise RuntimeError(str(e)) from e
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


def pn532_thru(conn, tag_cmd):
    apdu = [0xFF, 0x00, 0x00, 0x00, 2 + len(tag_cmd), 0xD4, 0x42] + list(tag_cmd)
    body, sw1, sw2 = _transmit(conn, apdu)
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"thru sw {sw1:02X} {sw2:02X}")
    if len(body) >= 3 and body[0] == 0xD5 and body[1] == 0x43:
        if body[2] != 0:
            raise RuntimeError(f"thru status {body[2]:02X}")
        return body[3:]
    return body


def read_pages(conn, page):
    data = pn532(conn, [0x30, page])
    return data[:16] if len(data) > 16 else data


def write_page(conn, page, four, check=True):
    try:
        pn532(conn, [0xA2, page] + list(four))
    except Exception as e:
        raise RuntimeError(f"write p{page}: {e}") from e
    time.sleep(0.08)


def cfg_pages_for_storage(size_byte):
    return CFG_PAGES.get(size_byte, CFG_PAGES[0x11])


def get_version(conn):
    data = pn532(conn, [0x60])
    return data


def cfg_pages(conn):
    try:
        ver = get_version(conn)
        if len(ver) >= 7 and ver[6] in CFG_PAGES:
            return cfg_pages_for_storage(ver[6])
    except Exception:
        pass
    for size in (0x11, 0x13, 0x0F):
        pages = CFG_PAGES[size]
        try:
            read_pages(conn, pages[0])
            return pages
        except Exception:
            continue
    return CFG_PAGES[0x0F]


def pwd_auth(conn, pwd):
    data = pn532_thru(conn, [0x1B] + list(pwd))
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


def inspect_tag(conn):
    uid = read_pages(conn, 0)
    cfg0 = None
    auth0 = 0xFF
    for p in (0x83, 0x29, 0xE3):
        try:
            data = read_pages(conn, p)
            cfg0 = p
            auth0 = data[3]
            break
        except Exception:
            continue
    print(f"inspect cfg0={cfg0} AUTH0={auth0:02x}", flush=True)
    return {"uid": uid[:8], "cfg0": cfg0, "auth0": auth0}


def prepare_write(reader):
    conn = connect_reader(reader)
    if conn is None:
        return None
    try:
        info = inspect_tag(conn)
    except Exception as e:
        print(f"inspect: {e}", flush=True)
        try:
            conn.disconnect()
        except Exception:
            pass
        return None
    if info["auth0"] > AUTH0_USER:
        print("auth: none (AUTH0 open)", flush=True)
        return conn
    try:
        pack = pwd_auth(conn, tag_pwd())
        print(f"auth: ours pack={pack.hex()}", flush=True)
        return conn
    except Exception as e:
        print(f"auth: ours {e}", flush=True)
        rf_cycle(conn)
    try:
        conn.disconnect()
    except Exception:
        pass
    conn = connect_reader(reader)
    if conn is None:
        return None
    try:
        inspect_tag(conn)
        pack = pwd_auth(conn, FACTORY_PWD)
        print(f"auth: factory pack={pack.hex()}", flush=True)
        return conn
    except Exception as e:
        print(f"auth: factory {e}", flush=True)
        rf_cycle(conn)
        try:
            conn.disconnect()
        except Exception:
            pass
        raise RuntimeError("tag is write-protected; password auth failed")


def unlock(conn):
    try:
        pwd_auth(conn, tag_pwd())
        return True
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
    write_page(conn, pwd_pg, pwd, check=False)
    write_page(conn, pack_pg, pack + b"\x00\x00", check=False)
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


def silence_reader(reader):
    conn = reader.createConnection()
    try:
        from smartcard.scard import SCARD_SHARE_DIRECT

        conn.connect(mode=SCARD_SHARE_DIRECT)
        _transmit(conn, BUZZER_OFF)
    except Exception:
        pass
    try:
        conn.disconnect()
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


def write_ndef(conn, member):
    pages = ndef.tag_pages(member)
    for i, page in enumerate(pages):
        if i == 0:
            continue
        write_page(conn, 3 + i, page)


def write_member(conn, member):
    write_ndef(conn, member)
    protect(conn)


def scan_report(conn):
    info = inspect_tag(conn)
    member = None
    try:
        member = read_member(conn)
    except Exception:
        member = None
    access = None
    if info["cfg0"] is not None:
        try:
            access = read_pages(conn, info["cfg0"] + 1)[0]
        except Exception:
            pass
    prot = None if access is None else bool(access & 0x80)
    standard = (
        info["auth0"] == AUTH0_USER
        and prot is False
        and member is not None
    )
    lines = ["OK" if standard else "REENROLL"]
    if member:
        lines.append(f"name={member.name}")
        lines.append(f"username={member.username}")
        lines.append(f"role={member.role}")
        lines.append(f"url={member.to_url()}")
    else:
        lines.extend(["name=", "username=", "role=", "url="])
    lines.append(f"AUTH0={info['auth0']:02x}")
    lines.append(f"ACCESS={access:02x}" if access is not None else "ACCESS=")
    lines.append(f"uid={info['uid'].hex()}")
    return "\n".join(lines) + "\n", member


def dump_tag(conn):
    lines = []
    for p in range(0, 16, 4):
        try:
            data = read_pages(conn, p)
            lines.append(f"{p:02x}: {data.hex()}")
        except Exception as e:
            lines.append(f"{p:02x}: {e}")
    for cfg0 in (0x29, 0x83, 0xE3):
        try:
            data = read_pages(conn, cfg0)
            lines.append(f"p{cfg0:02x}: {data[:4].hex()} AUTH0={data[3]:02x}")
        except Exception as e:
            lines.append(f"p{cfg0:02x}: {e}")
    try:
        acc = read_pages(conn, 0x84)
        lines.append(f"p84 ACCESS={acc[0]:02x}")
    except Exception as e:
        lines.append(f"p84 {e}")
    return "\n".join(lines) + "\n"


def connect_reader(reader):
    conn = reader.createConnection()
    try:
        conn.connect()
        return conn
    except Exception:
        return None


def rf_cycle(conn):
    try:
        _transmit(conn, [0xFF, 0x00, 0x00, 0x00, 0x04, 0xD4, 0x32, 0x01, 0x00])
        time.sleep(0.05)
        _transmit(conn, [0xFF, 0x00, 0x00, 0x00, 0x04, 0xD4, 0x32, 0x01, 0x01])
        time.sleep(0.05)
    except Exception:
        pass


def read_member_retry(reader, tries=8):
    for _ in range(tries):
        conn = connect_reader(reader)
        if conn is None:
            time.sleep(0.04)
            continue
        try:
            m = read_member(conn)
            if m:
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


class DumpReq:
    def __init__(self, reply_q):
        self.reply_q = reply_q


class ScanReq:
    def __init__(self, reply_q):
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
        silence_reader(reader)
        event_q.put(("status", "Hold your badge over the reader"))
        while _acr_reader() is not None:
            job = enroll_slot.take()
            if isinstance(job, ScanReq):
                event_q.put(("status", "Scan: hold tag"))
                done = False
                err = "timed out"
                for _ in range(1200):
                    conn = connect_reader(reader)
                    if conn:
                        try:
                            text, member = scan_report(conn)
                            verdict = text.split("\n")[0]
                            name = member.pronounce if member else "unknown"
                            spoken = "O K" if verdict == "OK" else "re enroll"
                            event_q.put(("speak", f"{name}. {spoken}"))
                            event_q.put(("status", verdict))
                            job.reply_q.put(text)
                            done = True
                        except Exception as e:
                            err = str(e)
                            job.reply_q.put(f"ERR {e}\n")
                            done = True
                        try:
                            conn.disconnect()
                        except Exception:
                            pass
                        while connect_reader(reader) is not None:
                            time.sleep(0.25)
                        break
                    time.sleep(0.25)
                if not done:
                    job.reply_q.put(f"ERR {err}\n")
                event_q.put(("status", "Hold your badge over the reader"))
                hold_status = False
                active = None
                continue
            if isinstance(job, DumpReq):
                event_q.put(("status", "Dump: hold tag"))
                dumped = False
                err = "timed out"
                for _ in range(80):
                    conn = connect_reader(reader)
                    if conn:
                        try:
                            text = dump_tag(conn)
                            job.reply_q.put(text)
                            event_q.put(("status", "Dump done"))
                            dumped = True
                        except Exception as e:
                            err = str(e)
                            job.reply_q.put(f"ERR {e}\n")
                            dumped = True
                        try:
                            conn.disconnect()
                        except Exception:
                            pass
                        hold_status = True
                        break
                    time.sleep(0.25)
                if not dumped:
                    job.reply_q.put(f"ERR {err}\n")
                    hold_status = True
                active = None
                continue
            if job:
                event_q.put(("status", f"Enroll {job.member.name}: tap tag to write"))
                ok = False
                err = "timed out"
                for _ in range(200):
                    conn = prepare_write(reader)
                    if conn:
                        try:
                            write_member(conn, job.member)
                            time.sleep(0.08)
                            back = read_member(conn)
                            if back != job.member:
                                raise RuntimeError("read-back mismatch")
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
