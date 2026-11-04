"""Check the Mach-O files of the macOS package without a Mac (used by build_mac.py).

For every Mach-O file (executable, dylib, Python extension) it reads the load commands and reports:
- architecture: the file must contain arm64 code (Apple silicon);
- code signature: arm64 code must be signed (at least ad hoc) or macOS refuses to run it;
- minimum macOS version;
- linked libraries: anything that is not in the system (/usr/lib, /System) and not inside the package
  (@rpath, @loader_path, @executable_path) would be missing on another Mac.
"""
import os
import struct
import sys

MH_MAGIC_64, FAT_MAGIC, FAT_MAGIC_64 = 0xFEEDFACF, 0xCAFEBABE, 0xCAFEBABF
CPU_ARM64, CPU_X86_64 = 0x0100000C, 0x01000007
LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB = 0xC, 0x80000018, 0x8000001F
LC_CODE_SIGNATURE, LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX = 0x1D, 0x32, 0x24
FILETYPES = {1: "object", 2: "executable", 6: "dylib", 8: "bundle"}


def _slices(data):
    magic = struct.unpack(">I", data[:4])[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        n = struct.unpack(">I", data[4:8])[0]
        out = []
        for i in range(n):
            if magic == FAT_MAGIC:
                cpu, _, off, size, _ = struct.unpack(">iiIII", data[8 + i * 20: 28 + i * 20])
            else:
                cpu, _, off, size, _, _ = struct.unpack(">iiQQII", data[8 + i * 32: 40 + i * 32])
            out.append((cpu & 0xFFFFFFFF, data[off: off + size]))
        return out
    if struct.unpack("<I", data[:4])[0] == MH_MAGIC_64:
        return [(struct.unpack("<I", data[4:8])[0], data)]
    return None


def _ver(v):
    return f"{v >> 16}.{(v >> 8) & 0xFF}"


def inspect(path):
    """None if not Mach-O, else a dict: archs, filetype, signed, minos, libs (for the arm64 slice)."""
    with open(path, "rb") as f:
        head = f.read(4)
        if len(head) < 4 or struct.unpack(">I", head)[0] not in (FAT_MAGIC, FAT_MAGIC_64) and \
                struct.unpack("<I", head)[0] != MH_MAGIC_64:
            return None
        data = head + f.read()
    sl = _slices(data)
    if not sl:
        return None
    archs = ["arm64" if c == CPU_ARM64 else "x86_64" if c == CPU_X86_64 else hex(c) for c, _ in sl]
    arm = [d for c, d in sl if c == CPU_ARM64]
    info = {"archs": archs, "filetype": None, "signed": False, "minos": None, "libs": []}
    if not arm:
        return info
    d = arm[0]
    _, _, _, ftype, ncmds, _, _, _ = struct.unpack("<IiiIIIII", d[:32])
    info["filetype"] = FILETYPES.get(ftype, str(ftype))
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack("<II", d[off: off + 8])
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            name_off = struct.unpack("<I", d[off + 8: off + 12])[0]
            name = d[off + name_off: off + size].split(b"\0", 1)[0].decode()
            info["libs"].append(name)
        elif cmd == LC_CODE_SIGNATURE:
            info["signed"] = True
        elif cmd == LC_BUILD_VERSION:
            info["minos"] = _ver(struct.unpack("<I", d[off + 12: off + 16])[0])
        elif cmd == LC_VERSION_MIN_MACOSX:
            info["minos"] = _ver(struct.unpack("<I", d[off + 8: off + 12])[0])
        off += size
    return info


def check_tree(root):
    """Returns (count, problems, executables, max_minos)."""
    problems, exes, count, max_minos = [], [], 0, (0, 0)
    for dirpath, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            info = inspect(p)
            if info is None:
                continue
            count += 1
            rel = os.path.relpath(p, root)
            if "arm64" not in info["archs"]:
                problems.append(f"{rel}: no arm64 code ({', '.join(info['archs'])})")
                continue
            if not info["signed"]:
                problems.append(f"{rel}: arm64 code is not signed")
            if info["filetype"] == "executable":
                exes.append(rel)
            if info["minos"]:
                mv = tuple(int(x) for x in info["minos"].split("."))
                max_minos = max(max_minos, mv)
            for lib in info["libs"]:
                if not lib.startswith(("/usr/lib/", "/System/", "@rpath/", "@loader_path/", "@executable_path/")):
                    problems.append(f"{rel}: links to {lib}, which is not on every Mac")
    return count, problems, sorted(exes), max_minos


if __name__ == "__main__":
    n, probs, exes, mo = check_tree(sys.argv[1])
    print(f"{n} Mach-O files; programs: {exes}; newest minimum macOS: {mo[0]}.{mo[1]}")
    print("\n".join(probs) if probs else "no problems")
