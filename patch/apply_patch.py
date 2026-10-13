#!/usr/bin/env python3
"""Apply the bundled Japanese-to-Chinese patch using Python 3's standard library."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import struct
import sys
import zipfile
import zlib


ROM_SIZE = 4 * 1024 * 1024
SOURCE_SHA256 = "6a68e1806d8d72accb4a5218330210e178880216863c1c38b8865032c5c28724"
TARGET_SHA256 = "2169067630de482a8a7a8a19f2851281969225efc7337a7400d9282fd1542e74"
PATCH_SHA256 = "774bc8c59c7be1aae1eda4a22b3407dd88633f91c2465a763b6a7dca18b972ff"
PATCH_NAME = "METAL_MAX_RETURNS_JP_TO_ZH.bps"


class PatchError(ValueError):
    """An input failed patch validation."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def apply_bps(source, patch):
    """Decode BPS, checking all three CRCs and every read/copy boundary."""
    if len(patch) < 19 or patch[:4] != b"BPS1":
        raise PatchError("不是有效的 BPS 补丁。")
    end = len(patch) - 12
    source_crc, target_crc, patch_crc = struct.unpack_from("<III", patch, end)
    if zlib.crc32(patch[:-4]) != patch_crc:
        raise PatchError("补丁 CRC 校验失败。")
    if zlib.crc32(source) != source_crc:
        raise PatchError("源文件 CRC 校验失败。")
    cursor = 4

    def number():
        nonlocal cursor
        value, shift = 0, 1
        while cursor < end:
            byte = patch[cursor]
            cursor += 1
            value += (byte & 0x7F) * shift
            if value > (1 << 63) - 1:
                raise PatchError("BPS 整数超出范围。")
            if byte & 0x80:
                return value
            shift <<= 7
            value += shift
        raise PatchError("BPS 整数被截断。")

    source_size, target_size, metadata_size = number(), number(), number()
    if source_size != len(source):
        raise PatchError("源文件大小与 BPS 声明不符。")
    if target_size > 64 * 1024 * 1024:
        raise PatchError("BPS 目标大小超出范围。")
    if metadata_size > end - cursor:
        raise PatchError("BPS 元数据被截断。")
    cursor += metadata_size
    target = bytearray()
    source_relative = target_relative = 0

    while len(target) < target_size:
        action = number()
        length, kind = (action >> 2) + 1, action & 3
        if length > target_size - len(target):
            raise PatchError("BPS 指令超出目标边界。")
        if kind == 0:  # SourceRead: source and output use the same offset.
            offset = len(target)
            if length > len(source) - offset:
                raise PatchError("SourceRead 超出源文件边界。")
            target.extend(source[offset:offset + length])
        elif kind == 1:  # TargetRead: literal bytes in the patch.
            if length > end - cursor:
                raise PatchError("TargetRead 数据被截断。")
            target.extend(patch[cursor:cursor + length])
            cursor += length
        else:
            encoded = number()
            distance = -(encoded >> 1) if encoded & 1 else encoded >> 1
            if kind == 2:  # SourceCopy.
                source_relative += distance
                if source_relative < 0 or source_relative + length > len(source):
                    raise PatchError("SourceCopy 超出源文件边界。")
                target.extend(source[source_relative:source_relative + length])
                source_relative += length
            else:  # TargetCopy deliberately permits overlapping copies.
                target_relative += distance
                if target_relative < 0 or target_relative >= len(target):
                    raise PatchError("TargetCopy 引用了尚未生成的数据。")
                for _ in range(length):
                    target.append(target[target_relative])
                    target_relative += 1

    if cursor != end:
        raise PatchError("BPS 指令区存在多余数据。")
    if zlib.crc32(target) != target_crc:
        raise PatchError("目标文件 CRC 校验失败。")
    return bytes(target)


def check_size(size):
    if size == ROM_SIZE + 512:
        raise PatchError("不支持带 512 字节复制头的 ROM；请使用 4 MiB 无头日版。")
    if size != ROM_SIZE:
        raise PatchError("源 ROM 必须为 4 MiB（4,194,304 字节）。")


def read_source(path):
    """Read a ROM or one unambiguous ROM member, without extracting a ZIP."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist()
                       if not item.is_dir()
                       and PurePosixPath(item.filename).suffix.lower() in (".sfc", ".smc")]
            if len(members) != 1:
                raise PatchError("ZIP 中必须且只能包含一个 .sfc 或 .smc 文件。")
            member = members[0]
            check_size(member.file_size)
            if member.flag_bits & 1:
                raise PatchError("不支持加密 ZIP。")
            with archive.open(member) as stream:
                data = stream.read(ROM_SIZE + 1)
    elif suffix in (".sfc", ".smc"):
        with path.open("rb") as stream:
            data = stream.read(ROM_SIZE + 513)
    else:
        raise PatchError("请输入 .sfc、.smc 或 ZIP 文件。")
    check_size(len(data))
    return data


def apply_release(source_path, output_path):
    source_path, output_path = Path(source_path), Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise PatchError("输出路径不能与源文件相同。")
    if output_path.exists() or output_path.is_symlink():
        raise PatchError("输出文件已存在；请使用新文件名。")
    source = read_source(source_path)
    if sha256(source) != SOURCE_SHA256:
        raise PatchError("源 ROM SHA-256 不匹配；需要指定的无头日版。")
    patch = Path(__file__).resolve().with_name(PATCH_NAME).read_bytes()
    if sha256(patch) != PATCH_SHA256:
        raise PatchError("随附补丁 SHA-256 不匹配。")
    target = apply_bps(source, patch)
    if len(target) != ROM_SIZE or sha256(target) != TARGET_SHA256:
        raise PatchError("补丁输出大小或 SHA-256 不匹配。")
    # Exclusive creation also closes the race after the earlier existence check.
    with output_path.open("xb") as stream:
        stream.write(target)
    return TARGET_SHA256


def main(argv=None):
    parser = argparse.ArgumentParser(description="应用重装机兵 Returns 中文补丁，不改写源文件。")
    parser.add_argument("source", help="4 MiB 无头日版 .sfc/.smc，或仅含一个此类 ROM 的 ZIP")
    parser.add_argument("output", help="新输出 ROM 的路径，例如 MetalMaxReturns-zh.sfc")
    args = parser.parse_args(argv)
    try:
        digest = apply_release(args.source, args.output)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1
    print("补丁应用成功。SHA-256：" + digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
