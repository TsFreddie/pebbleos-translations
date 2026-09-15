# SPDX-FileCopyrightText: 2024 Google LLC
# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Pebble font compiler, adapted from PebbleOS tools/font/fontgen.py."""

import itertools
import json
import re
import struct

import freetype

MIN_CODEPOINT = 0x20
MAX_2_BYTES_CODEPOINT = 0xFFFF
MAX_EXTENDED_CODEPOINT = 0x10FFFF
FONT_VERSION_3 = 3
WILDCARD_CODEPOINT = 0x25AF
ELLIPSIS_CODEPOINT = 0x2026
FEATURE_OFFSET_16 = 0x01
FEATURE_RLE4 = 0x02
HASH_TABLE_SIZE = 255
OFFSET_TABLE_MAX_SIZE = 128
MAX_GLYPHS_EXTENDED = HASH_TABLE_SIZE * OFFSET_TABLE_MAX_SIZE
MAX_GLYPHS = 256


def grouper(n, iterable, fillvalue=None):
    args = [iter(iterable)] * n
    return itertools.zip_longest(*args, fillvalue=fillvalue)


def bits(x):
    return [(x >> bit) & 1 for bit in reversed(range(8))]


class Font:
    def __init__(
        self, ttf_path, height, max_glyphs, max_glyph_size, legacy, baseline=None
    ):
        self.version = FONT_VERSION_3
        self.ttf_path = ttf_path
        self.max_height = int(height)
        self.baseline = int(baseline) if baseline is not None else self.max_height
        self.legacy = legacy
        try:
            self.face = freetype.Face(self.ttf_path)
            self.face.set_pixel_sizes(0, self.max_height)
        except freetype.FT_Exception as error:
            raise ValueError(f"Cannot load font {ttf_path}: {error}") from error
        self.wildcard_codepoint = WILDCARD_CODEPOINT
        self.number_of_glyphs = 0
        self.table_size = HASH_TABLE_SIZE
        self.tracking_adjust = 0
        self.regex = None
        self.codepoints = range(MIN_CODEPOINT, MAX_EXTENDED_CODEPOINT + 1)
        self.codepoint_bytes = 2
        self.max_glyphs = max_glyphs
        self.max_glyph_size = max_glyph_size
        self.glyph_table = []
        self.hash_table = [b""] * self.table_size
        self.offset_tables = [[] for _ in range(self.table_size)]
        self.offset_size_bytes = 4
        self.features = 0
        self.glyph_header = "<BBbbb"

    def set_compression(self, engine):
        if engine != "RLE4":
            raise ValueError(f"Unsupported compression engine: {engine}")
        self.features |= FEATURE_RLE4

    def set_tracking_adjust(self, adjust):
        self.tracking_adjust = adjust

    def set_regex_filter(self, regex_string):
        self.regex = re.compile(regex_string) if regex_string != ".*" else None

    def set_codepoint_list(self, list_path):
        with open(list_path) as codepoints_file:
            self.codepoints = {
                int(cp) for cp in json.load(codepoints_file)["codepoints"]
            }

    def compress_glyph_RLE4(self, bitmap):
        unit_list = [
            (name, len(list(group))) for name, group in itertools.groupby(bitmap)
        ]
        rle_unit_list = []
        for name, length in unit_list:
            while length > 0:
                unit_len = min(length, 8)
                rle_unit_list.append((name, unit_len))
                length -= unit_len

        num_units = len(rle_unit_list)
        if num_units % 2:
            rle_unit_list.append((0, 1))

        glyph_packed = []
        it = iter(rle_unit_list)
        for name, length in it:
            name2, length2 = next(it)
            packed_byte = name << 3 | (length - 1) | name2 << 7 | (length2 - 1) << 4
            glyph_packed.append(struct.pack("<B", packed_byte))
        while len(glyph_packed) % 4:
            glyph_packed.append(b"\0")
        return glyph_packed, num_units

    def check_decompress_glyph_RLE4(self, glyph_packed, width, rle_units):
        # Firmware decodes in-place, with the encoded glyph at the buffer's end.
        dst_ptr = struct.calcsize(self.glyph_header)
        src_ptr = self.max_glyph_size - len(glyph_packed)
        if src_ptr < dst_ptr:
            raise ValueError("Compressed glyph does not fit the glyph buffer")
        bitmap = [0] * self.max_glyph_size
        bitmap[src_ptr:] = [byte[0] for byte in glyph_packed]
        out_num_bits = 0
        out = 0
        while rle_units > 0:
            if src_ptr >= self.max_glyph_size:
                raise ValueError("Compressed glyph exceeds the glyph buffer")
            unit_pair = bitmap[src_ptr]
            src_ptr += 1
            for _ in range(min(rle_units, 2)):
                colour = (unit_pair >> 3) & 1
                length = (unit_pair & 0x07) + 1
                if colour:
                    out |= ((1 << length) - 1) << out_num_bits
                out_num_bits += length
                if out_num_bits >= 8:
                    if dst_ptr >= src_ptr or dst_ptr >= self.max_glyph_size:
                        raise ValueError("Glyph cannot be decompressed in-place")
                    bitmap[dst_ptr] = out & 0xFF
                    dst_ptr += 1
                    out >>= 8
                    out_num_bits -= 8
                unit_pair >>= 4
                rle_units -= 1
        if out_num_bits and dst_ptr >= self.max_glyph_size:
            raise ValueError("Decoded glyph exceeds the glyph buffer")

    def glyph_bits(self, codepoint, gindex):
        flags = (
            freetype.FT_LOAD_RENDER
            if self.legacy
            else freetype.FT_LOAD_RENDER
            | freetype.FT_LOAD_MONOCHROME
            | freetype.FT_LOAD_TARGET_MONO
        )
        try:
            self.face.load_glyph(gindex, flags)
        except freetype.FT_Exception as error:
            raise ValueError(
                f"Cannot render glyph U+{codepoint:04X}: {error}"
            ) from error
        bitmap = self.face.glyph.bitmap
        advance = self.face.glyph.advance.x // 64 + self.tracking_adjust
        width = bitmap.width
        height = bitmap.rows
        left = self.face.glyph.bitmap_left
        bottom = self.baseline - self.face.glyph.bitmap_top
        glyph_packed = []
        if height and width:
            glyph_bitmap = []
            if bitmap.pixel_mode == 1:
                for i in range(bitmap.rows):
                    row = []
                    for j in range(bitmap.pitch):
                        row.extend(bits(bitmap.buffer[i * bitmap.pitch + j]))
                    glyph_bitmap.extend(row[: bitmap.width])
            elif bitmap.pixel_mode == 2:
                glyph_bitmap = [1 if val > 127 else 0 for val in bitmap.buffer]
            else:
                raise ValueError(f"Unsupported pixel mode: {bitmap.pixel_mode}")

            if self.features & FEATURE_RLE4:
                glyph_packed, height = self.compress_glyph_RLE4(glyph_bitmap)
                if height > 255:
                    raise ValueError("Glyph requires more than 255 RLE4 units")
                self.check_decompress_glyph_RLE4(glyph_packed, width, height)
            else:
                for word in grouper(32, glyph_bitmap, 0):
                    w = sum(bit << index for index, bit in enumerate(word))
                    glyph_packed.append(struct.pack("<I", w))
                size = (width * height + 7) // 8
                if size > self.max_glyph_size:
                    raise ValueError(
                        f"Glyph U+{codepoint:04X} needs {size} bytes; "
                        f"the universal limit is {self.max_glyph_size} bytes"
                    )

        glyph_header = struct.pack(
            self.glyph_header, width, height, left, bottom, advance
        )
        return glyph_header + b"".join(glyph_packed)

    def fontinfo_bits(self):
        s = struct.Struct("<BBHHBBBB")
        return s.pack(
            self.version,
            self.max_height,
            self.number_of_glyphs,
            self.wildcard_codepoint,
            self.table_size,
            self.codepoint_bytes,
            s.size,
            self.features,
        )

    def build_tables(self):
        def add_glyph(codepoint, next_offset, gindex):
            offset = next_offset
            if gindex not in glyph_indices_lookup:
                glyph_bits = self.glyph_bits(codepoint, gindex)
                glyph_indices_lookup[gindex] = offset
                self.glyph_table.append(glyph_bits)
                next_offset += len(glyph_bits)
            else:
                offset = glyph_indices_lookup[gindex]
            if codepoint > MAX_2_BYTES_CODEPOINT:
                self.codepoint_bytes = 4
            self.number_of_glyphs += 1
            if self.number_of_glyphs > self.max_glyphs:
                raise ValueError(f"Font exceeds the {self.max_glyphs}-glyph limit")
            return offset, next_offset

        def codepoint_is_in_subset(codepoint):
            if codepoint not in (WILDCARD_CODEPOINT, ELLIPSIS_CODEPOINT):
                if self.regex is not None and self.regex.match(chr(codepoint)) is None:
                    return False
                if codepoint not in self.codepoints:
                    return False
            return True

        self.glyph_table.append(struct.pack("<I", 0))
        self.number_of_glyphs = 0
        glyph_indices_lookup = {}
        offset, next_offset = add_glyph(WILDCARD_CODEPOINT, 4, 0)
        glyph_entries = [(WILDCARD_CODEPOINT, offset)]
        codepoint, gindex = self.face.get_first_char()
        while gindex:
            if codepoint == WILDCARD_CODEPOINT:
                raise ValueError("Font uses the reserved wildcard codepoint")
            if codepoint_is_in_subset(codepoint):
                offset, next_offset = add_glyph(codepoint, next_offset, gindex)
                glyph_entries.append((codepoint, offset))
            codepoint, gindex = self.face.get_next_char(codepoint, gindex)

        if sum(len(glyph) for glyph in self.glyph_table) < 65536:
            self.features |= FEATURE_OFFSET_16
            self.offset_size_bytes = 2
        offset_format = "<"
        offset_format += "L" if self.codepoint_bytes == 4 else "H"
        offset_format += "L" if self.offset_size_bytes == 4 else "H"
        bucket_sizes = [0] * self.table_size
        for codepoint, offset in sorted(glyph_entries):
            glyph_hash = codepoint % self.table_size
            self.offset_tables[glyph_hash].append(
                struct.pack(offset_format, codepoint, offset)
            )
            bucket_sizes[glyph_hash] += 1
            if bucket_sizes[glyph_hash] >= OFFSET_TABLE_MAX_SIZE:
                raise ValueError("Font exceeds the hash bucket capacity")
        acc = 0
        for i, size in enumerate(bucket_sizes):
            self.hash_table[i] = struct.pack("<BBH", i, size, acc)
            acc += size * (self.offset_size_bytes + self.codepoint_bytes)

    def bitstring(self):
        return (
            self.fontinfo_bits()
            + b"".join(self.hash_table)
            + b"".join(b"".join(table) for table in self.offset_tables)
            + b"".join(self.glyph_table)
        )
