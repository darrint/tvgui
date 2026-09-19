import hashlib
import os
import tempfile
import unittest

from attendance import IN, OUT, Store
from member import Member, Role
from nfc import AUTH0_USER, cfg_pages_for_storage, tag_pack, tag_pwd
from tts import (
    PAUSE_SECS,
    _silence,
    _stitch,
    enrolled_phrase,
    greet_phrase,
    lead_prompt,
    wav_path,
)
import ndef


class MemberTests(unittest.TestCase):
    def test_roundtrip(self):
        m = Member.new("Darrin Thompson", "dthompson", "Darrin Thompson", Role.MENTOR)
        url = m.to_url()
        self.assertIn("role=mentor", url)
        self.assertEqual(Member.from_url(url), m)
        self.assertEqual(Member.from_uri_payload(m.to_uri_payload()), m)

    def test_default_role(self):
        m = Member.from_url(
            "https://members.teamroboto.org/?name=Jane+Doe&pronounce=Jane+Doe&username=jdoe"
        )
        self.assertEqual(m.role, Role.STUDENT)

    def test_parent_role(self):
        m = Member.new("Pat Parent", "pparent", None, Role.PARENT)
        self.assertEqual(m.role, Role.PARENT)
        self.assertIn("role=parent", m.to_url())
        self.assertEqual(Member.from_url(m.to_url()), m)


class NdefTests(unittest.TestCase):
    def test_pages(self):
        m = Member.new("Darrin Thompson", "dthompson", None, Role.MENTOR)
        pages = ndef.tag_pages(m)
        self.assertEqual(pages[0], ndef.CC)
        user = b"".join(pages[1:])
        self.assertEqual(ndef.parse_member_from_user_memory(user), m)


class TagPwdTests(unittest.TestCase):
    def setUp(self):
        os.environ["TVGUI_TAG_SECRET"] = "test-secret"

    def test_lengths(self):
        self.assertEqual(len(tag_pwd()), 4)
        self.assertEqual(len(tag_pack()), 2)
        self.assertEqual(AUTH0_USER, 0x04)

    def test_stable(self):
        key = hashlib.sha256(b"test-secret").digest()
        self.assertEqual(tag_pwd(), key[:4])
        self.assertEqual(tag_pack(), key[4:6])
        self.assertNotEqual(tag_pwd(), bytes([0xFF, 0xFF, 0xFF, 0xFF]))

    def test_missing_secret(self):
        del os.environ["TVGUI_TAG_SECRET"]
        with self.assertRaises(RuntimeError):
            tag_pwd()

    def test_cfg_pages(self):
        self.assertEqual(cfg_pages_for_storage(0x0F), (0x29, 0x2A, 0x2B, 0x2C))
        self.assertEqual(cfg_pages_for_storage(0x11), (0x83, 0x84, 0x85, 0x86))
        self.assertEqual(cfg_pages_for_storage(0x13), (0xE3, 0xE4, 0xE5, 0xE6))
        self.assertEqual(cfg_pages_for_storage(0x00), cfg_pages_for_storage(0x11))


class StoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.store = Store(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_toggle(self):
        m = Member.new("Jane Doe", "jdoe", None, Role.STUDENT)
        p = self.store.toggle(m, 1000, 2)
        self.assertEqual(p.direction, IN)
        self.assertEqual(self.store.who(), [(m, 1000)])
        p = self.store.toggle(m, 1003, 2)
        self.assertEqual(p.direction, OUT)
        self.assertEqual(self.store.who(), [])

    def test_debounce(self):
        m = Member.new("Jane Doe", "jdoe", None, Role.STUDENT)
        self.assertIsNotNone(self.store.toggle(m, 1000, 2))
        self.assertIsNone(self.store.toggle(m, 1001, 2))

    def test_people(self):
        m = Member.new("Jane Doe", "jdoe", "Jane", Role.STUDENT)
        self.store.upsert(m)
        self.assertEqual(self.store.people(), [m])


class TtsTests(unittest.TestCase):
    def test_greet_phrase(self):
        m = Member.new("Jane Doe", "jdoe", "Jane", Role.STUDENT)
        self.assertEqual(greet_phrase(m, IN), "Welcome student. Jane")
        self.assertEqual(greet_phrase(m, OUT), "good bye student. Jane")
        self.assertEqual(enrolled_phrase(m), "Jane enrolled")
        self.assertEqual(lead_prompt(m, IN), "_welcome-student")
        self.assertEqual(lead_prompt(m, OUT), "_goodbye-student")
        self.assertTrue(wav_path("jdoe", IN).endswith("jdoe-in.wav"))

    def test_stitch_pause(self):
        import wave

        d = tempfile.mkdtemp()
        a = os.path.join(d, "a.wav")
        b = os.path.join(d, "b.wav")
        out = os.path.join(d, "out.wav")
        pcm = b"\x00\x01" * 100
        for path in (a, b):
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(pcm)
        _stitch(out, (a, b))
        with wave.open(out, "rb") as w:
            frames = w.readframes(w.getnframes())
        self.assertEqual(len(frames), 400 + len(_silence(PAUSE_SECS)))


if __name__ == "__main__":
    unittest.main()
