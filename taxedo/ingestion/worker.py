from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import resource
import socket
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from taxedo.ingestion.documents import convert_document, ocr_image
from taxedo.security import CONVERSION_TIMEOUT, UserInputError


def restrict_filesystem():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError("Linux 5.13+ with Landlock is required for conversion")
    read = (1 << 0) | (1 << 2) | (1 << 3)
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= 1 << 13
    if abi >= 3:
        handled |= 1 << 14

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    attributes = Ruleset(handled)
    ruleset = libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
    if ruleset < 0:
        raise RuntimeError("Cannot create filesystem sandbox")
    try:
        paths = [
            Path(sys.prefix),
            Path(sys.base_prefix),
            Path("/usr"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc/ld.so.cache"),
            Path("/etc/fonts"),
            Path("/dev/null"),
            Path("/dev/urandom"),
            Path("/proc/cpuinfo"),
            Path("/proc/meminfo"),
            Path("/sys/devices/system/cpu"),
        ]
        package = Path(__file__).resolve().parents[1]
        paths += [
            package / "__init__.py",
            package / "security.py",
            package / "ingestion" / "__init__.py",
            package / "ingestion" / "documents.py",
        ]
        for path, permissions in [(path, read) for path in paths] + [
            (Path.cwd(), handled)
        ]:
            if not path.exists():
                continue
            if path.is_file() or not path.is_dir():
                permissions &= ~(
                    (1 << 3) | (1 << 4) | sum(1 << bit for bit in range(6, 14))
                )
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(permissions, descriptor)
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                    raise RuntimeError("Cannot allow converter runtime path")
            finally:
                os.close(descriptor)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, ruleset, 0) != 0:
            raise RuntimeError("Cannot activate filesystem sandbox")
    finally:
        os.close(ruleset)


def restrict_process():
    resource.setrlimit(resource.RLIMIT_CPU, (CONVERSION_TIMEOUT - 5,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
        restrict_filesystem()
        lib = ctypes.CDLL("libseccomp.so.2")
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        context = lib.seccomp_init(0x7FFF0000)
        if not context:
            raise RuntimeError("Cannot initialize conversion sandbox")
        try:
            for name in (
                b"socket",
                b"connect",
                b"ptrace",
                b"process_vm_readv",
                b"process_vm_writev",
            ):
                syscall = lib.seccomp_syscall_resolve_name(name)
                if (
                    syscall >= 0
                    and lib.seccomp_rule_add(
                        context, 0x00050000 | errno.EPERM, syscall, 0
                    )
                    != 0
                ):
                    raise RuntimeError("Cannot restrict converter")
            if lib.seccomp_load(context) != 0:
                raise RuntimeError("Cannot activate conversion sandbox")
        finally:
            lib.seccomp_release(context)
    else:

        class NoNetworkSocket(socket.socket):
            def __init__(self, *args, **kwargs):
                raise PermissionError("Network access is disabled during conversion")

        socket.socket = NoNetworkSocket


def main():
    try:
        restrict_process()
        data = Path(sys.argv[1]).read_bytes()
        text = (
            ocr_image(data)
            if sys.argv[2] == ".image"
            else convert_document(data, "input" + sys.argv[2], ocr_image)
        )
        print(json.dumps({"text": text}))
    except UserInputError as error:
        # Only messages written for the user leave the sandbox.
        print(json.dumps({"error": str(error)}))
        return 1
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
