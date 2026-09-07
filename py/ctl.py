import os
import socket
import threading
from queue import Queue, Empty

from member import Member, Role
from nfc import EnrollReq


def socket_path():
    env = os.environ.get("TVGUI_SOCK")
    if env:
        return env
    if os.path.isdir("/run/tvgui"):
        return "/run/tvgui/ctl.sock"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.path.join(runtime, "tvgui.sock")
    return "/tmp/tvgui.sock"


def listen(path, enroll_slot, event_q=None, state=None):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(4)
    while True:
        conn, _ = sock.accept()
        threading.Thread(
            target=_handle, args=(conn, enroll_slot, event_q, state), daemon=True
        ).start()


def _handle(conn, enroll_slot, event_q, state):
    try:
        f = conn.makefile("rwb")
        cmd = f.readline().decode().strip()
        if cmd == "BLANK":
            if event_q is None:
                f.write(b"ERR no kiosk\n")
            else:
                event_q.put(("blank",))
                f.write(b"OK\n")
            f.flush()
            return
        if cmd == "UNBLANK":
            if event_q is None:
                f.write(b"ERR no kiosk\n")
            else:
                event_q.put(("unblank",))
                f.write(b"OK\n")
            f.flush()
            return
        if cmd == "STATUS":
            f.write(_status_text(state).encode())
            f.flush()
            return
        if cmd != "ENROLL":
            f.write(b"ERR unknown command\n")
            f.flush()
            return
        name = f.readline().decode().strip()
        pronounce = f.readline().decode().strip()
        username = f.readline().decode().strip()
        role_s = f.readline().decode().strip()
        role = Role.parse(role_s)
        member = Member.new(name, username, pronounce, role) if role else None
        if not member:
            f.write(b"ERR bad enroll fields\n")
            f.flush()
            return
        reply = Queue()
        if not enroll_slot.offer(EnrollReq(member, reply)):
            f.write(b"ERR enroll already pending\n")
            f.flush()
            return
        try:
            err = reply.get(timeout=60)
        except Empty:
            enroll_slot.clear()
            f.write(b"ERR timed out\n")
            f.flush()
            return
        if err is None:
            f.write(b"OK\n")
        else:
            f.write(f"ERR {err}\n".encode())
        f.flush()
    except Exception as e:
        try:
            conn.sendall(f"ERR {e}\n".encode())
        except Exception:
            pass
    finally:
        conn.close()


def _status_text(state):
    if not state:
        return "blanked=0\nstatus=\nmentors=0\nstudents=0\n"
    with state["lock"]:
        return (
            f"blanked={int(state['blanked'])}\n"
            f"status={state['status']}\n"
            f"mentors={len(state['mentors'])}\n"
            f"students={len(state['students'])}\n"
        )


def _connect():
    path = socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except OSError as e:
        raise RuntimeError(f"connect {path}: {e} (is the kiosk running?)") from e
    return sock


def enroll_client(member):
    sock = _connect()
    sock.settimeout(65)
    sock.sendall(
        f"ENROLL\n{member.name}\n{member.pronounce}\n{member.username}\n{member.role}\n".encode()
    )
    line = sock.makefile().readline().strip()
    sock.close()
    if line == "OK":
        return
    raise RuntimeError(line[4:] if line.startswith("ERR ") else line)


def kiosk_cmd(cmd):
    sock = _connect()
    sock.settimeout(5)
    sock.sendall(cmd.encode() + b"\n")
    data = sock.makefile().read()
    sock.close()
    return data
