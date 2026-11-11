import ctypes
import json
import os
import platform


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    ruleset = Ruleset((1 << 13) - 1)
    fd = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    result = {
        "platform": platform.platform(),
        "landlock_ruleset_fd": fd,
        "errno": ctypes.get_errno(),
        "error": os.strerror(ctypes.get_errno()) if fd < 0 else None,
        "landlock_available": fd >= 0,
    }
    if fd >= 0:
        os.close(fd)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
