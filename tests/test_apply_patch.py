"""Run with: python -m unittest discover -s tests -v"""

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "patch" / "apply_patch.py"
SPEC = importlib.util.spec_from_file_location("release_patch", SCRIPT)
patcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)


def integer(value):
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if not value:
            result.append(byte | 0x80)
            return bytes(result)
        result.append(byte)
        value -= 1


def action(kind, length):
    return integer(((length - 1) << 2) | kind)


def fixture(source, target, commands, source_crc=None, target_crc=None):
    body = (b"BPS1" + integer(len(source)) + integer(len(target))
            + integer(0) + commands)
    body += struct.pack("<II", zlib.crc32(source) if source_crc is None else source_crc,
                        zlib.crc32(target) if target_crc is None else target_crc)
    return body + struct.pack("<I", zlib.crc32(body))


class DecoderTests(unittest.TestCase):
    def test_all_four_actions_and_overlapping_target_copy(self):
        source = b"ABCDEFGHIJKLMNOP"
        target = b"ABCxyFGHIxyFGHIxyFGHI"
        commands = (action(0, 3) + action(1, 2) + b"xy"
                    + action(2, 4) + integer(5 << 1)
                    + action(3, 12) + integer(3 << 1))
        self.assertEqual(patcher.apply_bps(source, fixture(source, target, commands)), target)

    def test_negative_source_copy_distance(self):
        source, target = b"0123456789", b"567234"
        commands = action(2, 3) + integer(10) + action(2, 3) + integer(13)
        self.assertEqual(patcher.apply_bps(source, fixture(source, target, commands)), target)

    def test_negative_target_copy_distance(self):
        source, target = b"", b"ABABABAB"
        commands = (action(1, 2) + b"AB" + action(3, 2) + integer(0)
                    + action(3, 4) + integer(5))
        self.assertEqual(patcher.apply_bps(source, fixture(source, target, commands)), target)

    def test_rejects_all_three_bad_crcs(self):
        source = target = b"ABCD"
        valid = fixture(source, target, action(0, 4))
        bad_patch = valid[:-1] + bytes([valid[-1] ^ 1])
        cases = [bad_patch,
                 fixture(source, target, action(0, 4), source_crc=0),
                 fixture(source, target, action(0, 4), target_crc=0)]
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(patcher.PatchError):
                patcher.apply_bps(source, candidate)

    def test_rejects_wrong_source(self):
        patch = fixture(b"ABCD", b"ABCD", action(0, 4))
        with self.assertRaises(patcher.PatchError):
            patcher.apply_bps(b"WXYZ", patch)

    def test_rejects_invalid_copy_and_literal_boundaries(self):
        cases = [(b"A", b"AA", action(0, 2)),
                 (b"A", b"B", action(1, 1)),
                 (b"A", b"B", action(2, 1) + integer(2)),
                 (b"A", b"B", action(2, 1) + integer(3)),
                 (b"", b"A", action(3, 1) + integer(0)),
                 (b"A", b"A", action(0, 2)),
                 (b"A", b"A", action(0, 1) + b"\x80")]
        for source, target, commands in cases:
            with self.subTest(commands=commands), self.assertRaises(patcher.PatchError):
                patcher.apply_bps(source, fixture(source, target, commands))


class FileTests(unittest.TestCase):
    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.sfc", Path(folder) / "output.sfc"
            source.write_bytes(b"source")
            output.write_bytes(b"keep this")
            with self.assertRaisesRegex(patcher.PatchError, "已存在"):
                patcher.apply_release(source, output)
            self.assertEqual(output.read_bytes(), b"keep this")
            self.assertEqual(source.read_bytes(), b"source")

    def test_source_cannot_be_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.sfc"
            source.write_bytes(b"keep this")
            with self.assertRaisesRegex(patcher.PatchError, "不能与源文件相同"):
                patcher.apply_release(source, source)
            self.assertEqual(source.read_bytes(), b"keep this")

    def test_headered_rom_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "headered.smc"
            source.write_bytes(bytes(patcher.ROM_SIZE + 512))
            with self.assertRaisesRegex(patcher.PatchError, "512"):
                patcher.read_source(source)

    def test_wrong_release_source_creates_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "wrong.sfc", Path(folder) / "output.sfc"
            source.write_bytes(bytes(patcher.ROM_SIZE))
            with self.assertRaisesRegex(patcher.PatchError, "SHA-256"):
                patcher.apply_release(source, output)
            self.assertFalse(output.exists())

    def test_unique_zip_member_and_ambiguous_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.zip"
            data = bytes(patcher.ROM_SIZE)
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("nested/ROM.SFC", data)
                archive.writestr("readme.txt", "example")
            self.assertEqual(patcher.read_source(source), data)
            with zipfile.ZipFile(source, "a") as archive:
                archive.writestr("second.smc", b"other")
            with self.assertRaisesRegex(patcher.PatchError, "只能包含一个"):
                patcher.read_source(source)


if __name__ == "__main__":
    unittest.main()
