"""Bounded in-memory PDF geometry cache; stores no passwords or page pixels."""
from collections import OrderedDict

_SIZES = OrderedDict()
_MAX_PAGES = 50_000
_MAX_DOCUMENTS = 8


def signature(path):
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_size,
                stat.st_ctime_ns, stat.st_ino)
    except OSError:
        return None


def get_sizes(key, count):
    value = _SIZES.pop(key, None)
    if value is None or len(value) != count:
        return None
    _SIZES[key] = value
    return value


def put_sizes(key, sizes):
    if key is None or len(sizes) > _MAX_PAGES:
        return
    for old in list(_SIZES):
        if old[0] == key[0]:
            _SIZES.pop(old)
    _SIZES[key] = tuple((size.width(), size.height()) for size in sizes)
    while len(_SIZES) > _MAX_DOCUMENTS or sum(map(len, _SIZES.values())) > _MAX_PAGES:
        _SIZES.popitem(last=False)
