# Copyright: 2005-2011 Brian Harring <ferringb@gmail.com>
# License: GPL2/BSD

"""
interaction with the livefs: generating fs objects to represent the livefs.
"""

import os, collections, errno
from stat import S_IMODE, S_ISDIR, S_ISREG, S_ISLNK, S_ISFIFO

from pkgcore.fs.fs import (
    fsFile, fsDir, fsSymlink, fsDev, fsFifo, get_major_minor, fsBase)
from pkgcore.fs.contents import contentsSet
from snakeoil.chksum import get_handlers
from snakeoil.data_source import local_source

from snakeoil.osutils import normpath, join as pjoin
from snakeoil.mappings import LazyValDict
from snakeoil.osutils import listdir

__all__ = ["gen_obj", "scan", "iter_scan"]


def gen_chksums(handlers, location):
    def f(key):
        return handlers[key](location)
    return LazyValDict(handlers, f)


def gen_obj(path, stat=None, chksum_handlers=None, real_location=None,
    stat_func=os.lstat):

    """
    given a fs path, and an optional stat, create an appropriate fs obj.

    :param stat: stat object to reuse if available
    :param real_location: real path to the object if path is the desired
        location, rather then existant location.
    :raise KeyError: if no obj type matches the stat checks
    :return: :obj:`pkgcore.fs.fs.fsBase` derivative
    """

    if real_location is None:
        real_location = path
    if stat is None:
        try:
            stat = stat_func(real_location)
        except EnvironmentError, e:
            if stat_func == os.lstat or e.errno != errno.ENOENT:
                raise
            stat = os.lstat(real_location)

    mode = stat.st_mode
    d = {"mtime":stat.st_mtime, "mode":S_IMODE(mode),
         "uid":stat.st_uid, "gid":stat.st_gid}
    if S_ISREG(mode):
        d["size"] = stat.st_size
        d["data"] = local_source(real_location)
        if chksum_handlers is not None:
            d["chf_types"] = chksum_handlers
        return fsFile(path, **d)
    elif S_ISDIR(mode):
        return fsDir(path, **d)
    elif S_ISLNK(mode):
        d["target"] = os.readlink(real_location)
        return fsSymlink(path, **d)
    elif S_ISFIFO(mode):
        return fsFifo(path, **d)
    else:
        major, minor = get_major_minor(stat)
        d["minor"] = minor
        d["major"] = major
        d["mode"] = mode
        return fsDev(path, **d)


# hmm. this code is roughly 25x slower then find.
# make it less slow somehow. the obj instantiation is a bit of a
# killer I'm afraid; without obj, looking at 2.3ms roughly best of 3
# 100 iterations, obj instantiation, 58ms.
# also, os.path.join is rather slow.
# in this case, we know it's always pegging one more dir on, so it's
# fine doing it this way (specially since we're relying on
# os.path.sep, not '/' :P)

def _internal_iter_scan(path, chksum_handlers, stat_func=os.lstat):
    dirs = collections.deque([normpath(path)])
    obj = gen_obj(dirs[0], chksum_handlers=chksum_handlers,
        stat_func=stat_func)
    yield obj
    if not obj.is_dir:
        return
    while dirs:
        base = dirs.popleft()
        for x in listdir(base):
            path = pjoin(base, x)
            obj = gen_obj(path, chksum_handlers=chksum_handlers,
                        real_location=path, stat_func=stat_func)
            yield obj
            if obj.is_dir:
                dirs.append(path)


def _internal_offset_iter_scan(path, chksum_handlers, offset,
    stat_func=os.lstat):
    offset = normpath(offset)
    path = normpath(path)
    dirs = collections.deque([path[len(offset):]])
    if dirs[0]:
        yield gen_obj(dirs[0], chksum_handlers=chksum_handlers,
            stat_func=stat_func)

    sep = os.path.sep
    while dirs:
        base = dirs.popleft()
        real_base = pjoin(offset, base.lstrip(sep))
        base = base.rstrip(sep) + sep
        for x in listdir(real_base):
            path = pjoin(base, x)
            obj = gen_obj(path, chksum_handlers=chksum_handlers,
                        real_location=pjoin(real_base, x),
                        stat_func=os.lstat)
            yield obj
            if obj.is_dir:
                dirs.append(path)


def iter_scan(path, offset=None, follow_symlinks=False, chksum_types=None):
    """
    Recursively scan a path.

    Does not follow symlinks pointing at dirs, just merely yields an
    obj representing said symlink

    :return: an iterator of :obj:`pkgcore.fs.fs.fsBase` objects.

    :param path: str path of what directory to scan in the livefs
    :param offset: if not None, prefix to strip from each objects location.
        if offset is /tmp, /tmp/blah becomes /blah
    """
    chksum_handlers = get_handlers(chksum_types)

    stat_func = follow_symlinks and os.stat or os.lstat
    if offset is None:
        return _internal_iter_scan(path, chksum_handlers, stat_func)
    return _internal_offset_iter_scan(path, chksum_handlers, offset, stat_func)


def scan(*a, **kw):
    """
    calls list(iter_scan(*a, **kw))
    Look at iter_scan for valid args
    """
    mutable = kw.pop("mutable", True)
    return contentsSet(iter_scan(*a, **kw), mutable=mutable)

class _realpath_dir(object):

    _realpath_func = staticmethod(os.path.realpath)

    def __init__(self):
        self._cache = {}

    def __call__(self, location):
        dname, fname = location.rsplit("/", 1)
        if not dname:
            return location
        dname2 = self._cache.get(dname)
        if dname2 is None:
            dname2 = self._cache[dname] = self._realpath_func(dname)
        return pjoin(dname2, fname)


def intersect(cset, realpath=False):
    """generate the intersect of a cset and the livefs"""
    f = gen_obj
    if realpath:
        f2 = _realpath_dir()
    else:
        f2 = lambda x:x
    for x in cset:
        try:
            yield f(f2(x.location))
        except OSError, oe:
            if oe.errno not in (errno.ENOENT, errno.ENOTDIR):
                raise
            del oe


def recursively_fill_syms(cset, limiter=fsBase):
    sym_src = [cset.links()]
    while sym_src:
        syms = sym_src.pop(-1)
        new_syms = []
        for sym in syms:
            new_loc = sym.resolved_target
            if new_loc in cset:
                continue
            try:
                obj = gen_obj(new_loc)
            except EnvironmentError, e:
                if e.errno != errno.ENOENT:
                    raise
                continue
            if obj.is_sym:
                cset.add(obj)
                new_syms.append(obj)
            elif isinstance(obj, limiter):
                cset.add(obj)

        if new_syms:
            sym_src.append(new_syms)
