"""Bounded in-memory PDF geometry cache; stores no passwords or page pixels."""
import os
from collections import OrderedDict


def _change_time(path):
    """NTFS change time detects in-place writes even after mtime is restored."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ("creation", "access", "write", "change")] + [
                        ("attributes", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handle = create(str(path), 0x80, 7, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        return None
    try:
        info = BasicInfo()
        return info.change if query(handle, 0, ctypes.byref(info), ctypes.sizeof(info)) else None
    finally:
        close(handle)

_SIZES = OrderedDict()
_MAX_PAGES = 50_000
_MAX_DOCUMENTS = 8


def path_key(path):
    return os.path.normcase(str(path.resolve()))


def signature(path):
    try:
        stat = path.stat()
        return (path_key(path), stat.st_mtime_ns, stat.st_size,
                stat.st_ctime_ns, stat.st_ino, _change_time(path))
    except OSError:
        return None


def get_sizes(key, count):
    value = get_partial_sizes(key, count)
    if value is None or any(size is None for size in value):
        return None
    return value


def get_partial_sizes(key, count):
    value = _SIZES.pop(key, None)
    if value is None or len(value) != count:
        return None
    _SIZES[key] = value
    return value


def put_sizes(key, sizes):
    put_partial_sizes(key, sizes, set())


def put_partial_sizes(key, sizes, pending):
    if key is None or len(sizes) > _MAX_PAGES:
        return
    for old in list(_SIZES):
        if old[0] == key[0]:
            _SIZES.pop(old)
    _SIZES[key] = tuple(None if index in pending else (size.width(), size.height())
                        for index, size in enumerate(sizes))
    while len(_SIZES) > _MAX_DOCUMENTS or sum(map(len, _SIZES.values())) > _MAX_PAGES:
        _SIZES.popitem(last=False)


def invalidate(path):
    normalized = path_key(path)
    for key in list(_SIZES):
        if key[0] == normalized:
            _SIZES.pop(key)
