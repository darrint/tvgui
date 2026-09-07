from member import Member

NDEF_URI = 0x55
TLV_NDEF = 0x03
TLV_TERM = 0xFE
CC = bytes([0xE1, 0x10, 0x3E, 0x00])


def encode_uri_message(payload):
    rec = bytearray([0xD1, 0x01, len(payload), NDEF_URI])
    rec.extend(payload)
    return bytes(rec)


def encode_tag(member):
    msg = encode_uri_message(member.to_uri_payload())
    tlv = bytearray([TLV_NDEF])
    if len(msg) < 255:
        tlv.append(len(msg))
    else:
        tlv.append(0xFF)
        tlv.extend(len(msg).to_bytes(2, "big"))
    tlv.extend(msg)
    tlv.append(TLV_TERM)
    return bytes(tlv)


def parse_uri_payload(msg):
    if len(msg) < 5 or msg[1] != 0x01 or msg[3] != NDEF_URI:
        return None
    if (msg[0] & 0x07) != 0x01:
        return None
    plen = msg[2]
    if 4 + plen > len(msg):
        return None
    return msg[4:4 + plen]


def _tlv_len(rest):
    if not rest:
        return None
    if rest[0] == 0xFF:
        if len(rest) < 3:
            return None
        return int.from_bytes(rest[1:3], "big"), 3
    return rest[0], 1


def parse_tlv(user):
    i = 0
    while i < len(user):
        t = user[i]
        if t == 0x00:
            i += 1
            continue
        if t == TLV_TERM:
            break
        i += 1
        parsed = _tlv_len(user[i:])
        if parsed is None:
            return None
        length, n = parsed
        i += n
        if t == TLV_NDEF:
            return user[i:i + length]
        i += length
    return None


def parse_member_from_user_memory(user):
    msg = parse_tlv(user)
    if msg is None:
        return None
    payload = parse_uri_payload(msg)
    if payload is None:
        return None
    return Member.from_uri_payload(payload)


def tag_pages(member):
    raw = bytearray(CC)
    raw.extend(encode_tag(member))
    while len(raw) % 4:
        raw.append(0)
    pages = []
    for i in range(0, len(raw), 4):
        pages.append(bytes(raw[i:i + 4]))
    return pages
