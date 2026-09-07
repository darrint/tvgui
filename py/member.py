BASE = "https://members.teamroboto.org/"
URI_HTTPS = 0x04


class Role:
    MENTOR = "mentor"
    STUDENT = "student"
    PARENT = "parent"

    @staticmethod
    def parse(s):
        v = (s or "").strip().lower()
        if v == Role.MENTOR:
            return Role.MENTOR
        if v == Role.STUDENT:
            return Role.STUDENT
        if v == Role.PARENT:
            return Role.PARENT
        return None


class Member:
    def __init__(self, name, username, pronounce, role):
        self.name = name
        self.username = username
        self.pronounce = pronounce
        self.role = role

    def __eq__(self, other):
        return (
            isinstance(other, Member)
            and self.name == other.name
            and self.username == other.username
            and self.pronounce == other.pronounce
            and self.role == other.role
        )

    @staticmethod
    def new(name, username, pronounce=None, role=Role.STUDENT):
        name = (name or "").strip()
        username = (username or "").strip()
        if not name or not username:
            return None
        if Role.parse(role) is None:
            return None
        role = Role.parse(role)
        p = (pronounce or "").strip() or name
        return Member(name, username, p, role)

    def to_url(self):
        return (
            f"{BASE}?name={_form_encode(self.name)}"
            f"&pronounce={_form_encode(self.pronounce)}"
            f"&username={_form_encode(self.username)}"
            f"&role={self.role}"
        )

    @staticmethod
    def from_url(url):
        url = (url or "").strip()
        if "?" not in url:
            return None
        q = url.split("?", 1)[1]
        name = pronounce = username = None
        role = Role.STUDENT
        for part in q.split("&"):
            if "=" not in part:
                return None
            k, v = part.split("=", 1)
            v = _form_decode(v)
            if k == "name":
                name = v
            elif k == "pronounce":
                pronounce = v
            elif k == "username":
                username = v
            elif k == "role":
                parsed = Role.parse(v)
                if parsed:
                    role = parsed
        return Member.new(name, username, pronounce, role)

    def uri_suffix(self):
        url = self.to_url()
        return url[8:] if url.startswith("https://") else url

    def to_uri_payload(self):
        return bytes([URI_HTTPS]) + self.uri_suffix().encode("utf-8")

    @staticmethod
    def from_uri_payload(payload):
        if not payload or payload[0] != URI_HTTPS:
            return None
        rest = payload[1:].decode("utf-8", errors="strict")
        return Member.from_url("https://" + rest)


def _form_encode(s):
    out = []
    for b in s.encode("utf-8"):
        if (ord("A") <= b <= ord("Z") or ord("a") <= b <= ord("z")
                or ord("0") <= b <= ord("9") or b in (ord("-"), ord("_"), ord("."))):
            out.append(chr(b))
        elif b == ord(" "):
            out.append("+")
        else:
            out.append(f"%{b:02X}")
    return "".join(out)


def _form_decode(s):
    out = bytearray()
    b = s.encode("ascii", errors="replace")
    i = 0
    while i < len(b):
        if b[i] == ord("+"):
            out.append(ord(" "))
            i += 1
        elif b[i] == ord("%") and i + 2 < len(b):
            try:
                out.append(int(b[i + 1:i + 3], 16))
                i += 3
            except ValueError:
                out.append(b[i])
                i += 1
        else:
            out.append(b[i])
            i += 1
    return out.decode("utf-8", errors="replace")
