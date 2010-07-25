# Copyright: 2006-2008 Brian Harring <ferringb@gmail.com>
# License: GPL2/BSD

"""
gentoo/ebuild specific triggers
"""

import os, errno
from pkgcore.merge import triggers, const, errors
from pkgcore.fs import livefs
from pkgcore.restrictions import values

from snakeoil.osutils import normpath
from snakeoil.fileutils import read_bash_dict, AtomicWriteFile
from snakeoil.osutils import listdir_files
from snakeoil.lists import stable_unique, iflatten_instance
from snakeoil.osutils import join as pjoin

from snakeoil.demandload import demandload
demandload(globals(),
    "fnmatch",
    'pkgcore:os_data',
    'pkgcore.merge.engine:map_new_cset_livefs')

colon_parsed = frozenset(
    ["ADA_INCLUDE_PATH",  "ADA_OBJECTS_PATH", "INFODIR", "INFOPATH",
     "LDPATH", "MANPATH", "PATH", "PRELINK_PATH", "PRELINK_PATH_MASK",
     "PYTHONPATH", "PKG_CONFIG_PATH", "ROOTPATH"])

incrementals = frozenset(
    ['ADA_INCLUDE_PATH', 'ADA_OBJECTS_PATH', 'CLASSPATH', 'CONFIG_PROTECT',
     'CONFIG_PROTECT_MASK', 'INFODIR', 'INFOPATH', 'KDEDIRS', 'LDPATH',
     'MANPATH', 'PATH', 'PRELINK_PATH', 'PRELINK_PATH_MASK', 'PYTHONPATH',
     'ROOTPATH', 'PKG_CONFIG_PATH'])

default_ldpath = ('/lib', '/lib64', '/lib32',
    '/usr/lib', '/usr/lib64', '/usr/lib32')

def collapse_envd(base):
    collapsed_d = {}
    try:
        env_d_files = sorted(listdir_files(base))
    except OSError, oe:
        if oe.errno != errno.ENOENT:
            raise
    else:
        for x in env_d_files:
            if x.endswith(".bak") or x.endswith("~") or x.startswith("._cfg") \
                or len(x) <= 2 or not x[0:2].isdigit():
                continue
            d = read_bash_dict(pjoin(base, x))
            # inefficient, but works.
            for k, v in d.iteritems():
                collapsed_d.setdefault(k, []).append(v)
            del d

    loc_incrementals = set(incrementals)
    loc_colon_parsed = set(colon_parsed)

    # split out env.d defined incrementals..
    # update incrementals *and* colon parsed for colon_seperated;
    # incrementals on it's own is space seperated.

    for x in collapsed_d.pop("COLON_SEPARATED", []):
        v = x.split()
        if v:
            loc_colon_parsed.update(v)

    loc_incrementals.update(loc_colon_parsed)

    # now space.
    for x in collapsed_d.pop("SPACE_SEPARATED", []):
        v = x.split()
        if v:
            loc_incrementals.update(v)

    # now reinterpret.
    for k, v in collapsed_d.iteritems():
        if k not in loc_incrementals:
            collapsed_d[k] = v[-1]
            continue
        if k in loc_colon_parsed:
            collapsed_d[k] = filter(None, iflatten_instance(
                x.split(':') for x in v))
        else:
            collapsed_d[k] = filter(None, iflatten_instance(
                x.split() for x in v))

    return collapsed_d, loc_incrementals, loc_colon_parsed


def string_collapse_envd(envd_dict, incrementals, colon_incrementals):
    """transform a passed in dict to strictly strings"""
    for k, v in envd_dict.iteritems():
        if k not in incrementals:
            continue
        if k in colon_incrementals:
            envd_dict[k] = ':'.join(v)
        else:
            envd_dict[k] = ' '.join(v)


def update_ldso(ld_search_path, offset='/'):
    # we do an atomic rename instead of open and write quick
    # enough (avoid the race iow)
    fp = pjoin(offset, 'etc', 'ld.so.conf')
    new_f = AtomicWriteFile(fp, uid=os_data.root_uid, gid=os_data.root_uid, perms=0644)
    new_f.write("# automatically generated, edit env.d files instead\n")
    new_f.writelines(x.strip()+"\n" for x in ld_search_path)
    new_f.close()


class env_update(triggers.base):

    required_csets = ()
    priority = 5
    _hooks = ('post_unmerge', 'post_merge')

    def trigger(self, engine):
        offset = engine.offset
        d, inc, colon = collapse_envd(pjoin(offset, "etc/env.d"))

        l = d.pop("LDPATH", None)
        if l is not None:
            update_ldso(l, engine.offset)

        string_collapse_envd(d, inc, colon)

        new_f = AtomicWriteFile(pjoin(offset, "etc", "profile.env"), uid=os_data.root_uid, gid=os_data.root_gid, perms=0644)
        new_f.write("# autogenerated.  update env.d instead\n")
        new_f.writelines('export %s="%s"\n' % (k, d[k]) for k in sorted(d))
        new_f.close()
        new_f = AtomicWriteFile(pjoin(offset, "etc", "profile.csh"), uid=os_data.root_uid, gid=os_data.root_gid, perms=0644)
        new_f.write("# autogenerated, update env.d instead\n")
        new_f.writelines('setenv %s="%s"\n' % (k, d[k]) for k in sorted(d))
        new_f.close()


def simple_chksum_compare(x, y):
    found = False
    for k, v in x.chksums.iteritems():
        if k == "size":
            continue
        o = y.chksums.get(k)
        if o is not None:
            if o != v:
                return False
            found = True
    if "size" in x.chksums and "size" in y.chksums:
        return x.chksums["size"] == y.chksums["size"]
    return found


def gen_config_protect_filter(offset, extra_protects=(), extra_disables=()):
    collapsed_d, inc, colon = collapse_envd(pjoin(offset, "etc/env.d"))
    collapsed_d.setdefault("CONFIG_PROTECT", []).extend(extra_protects)
    collapsed_d.setdefault("CONFIG_PROTECT_MASK", []).extend(extra_disables)

    r = [values.StrGlobMatch(normpath(x).rstrip("/") + "/")
         for x in set(stable_unique(collapsed_d["CONFIG_PROTECT"] + ["/etc"]))]
    if len(r) > 1:
        r = values.OrRestriction(*r)
    else:
        r = r[0]
    neg = stable_unique(collapsed_d["CONFIG_PROTECT_MASK"])
    if neg:
        if len(neg) == 1:
            r2 = values.StrGlobMatch(normpath(neg[0]).rstrip("/") + "/",
                                     negate=True)
        else:
            r2 = values.OrRestriction(
                negate=True,
                *[values.StrGlobMatch(normpath(x).rstrip("/") + "/")
                  for x in set(neg)])
        r = values.AndRestriction(r, r2)
    return r


class ConfigProtectInstall(triggers.base):

    required_csets = ('install_existing', 'install')
    priority = 90
    _hooks = ('pre_merge',)

    def __init__(self, extra_protects=(), extra_disables=()):
        triggers.base.__init__(self)
        self.renames = {}
        self.extra_protects = extra_protects
        self.extra_disables = extra_disables

    def register(self, engine):
        triggers.base.register(self, engine)
        t2 = ConfigProtectInstall_restore(self.renames)
        t2.register(engine)

    def trigger(self, engine, existing_cset, install_cset):
        # hackish, but it works.
        protected_filter = gen_config_protect_filter(engine.offset,
            self.extra_protects, self.extra_disables).match
        protected = {}

        for x in existing_cset.iterfiles():
            if x.location.endswith("/.keep"):
                continue
            elif protected_filter(x.location):
                replacement = install_cset[x]
                if not simple_chksum_compare(replacement, x):
                    protected.setdefault(
                        pjoin(engine.offset,
                              os.path.dirname(x.location).lstrip(os.path.sep)),
                        []).append((os.path.basename(replacement.location),
                                    replacement))

        for dir_loc, entries in protected.iteritems():
            updates = dict((x[0], []) for x in entries)
            try:
                existing = sorted(x for x in listdir_files(dir_loc)
                    if x.startswith("._cfg"))
            except OSError, oe:
                if oe.errno != errno.ENOENT:
                    raise
                # this shouldn't occur.
                continue

            for x in existing:
                try:
                    # ._cfg0000_filename
                    count = int(x[5:9])
                    if x[9] != "_":
                        raise ValueError
                    fn = x[10:]
                except (ValueError, IndexError):
                    continue
                if fn in updates:
                    updates[fn].append((count, fn))


            # now we rename.
            for fname, entry in entries:
                # check for any updates with the same chksums.
                count = 0
                for cfg_count, cfg_fname in updates[fname]:
                    if simple_chksum_compare(livefs.gen_obj(
                            pjoin(dir_loc, cfg_fname)), entry):
                        count = cfg_count
                        break
                    count = max(count, cfg_count + 1)
                try:
                    install_cset.remove(entry)
                except KeyError:
                    # this shouldn't occur...
                    continue
                new_fn = pjoin(dir_loc, "._cfg%04i_%s" % (count, fname))
                new_entry = entry.change_attributes(location=new_fn)
                install_cset.add(new_entry)
                self.renames[new_entry] = entry
            del updates


class ConfigProtectInstall_restore(triggers.base):

    required_csets = ('install',)
    priority = 10
    _hooks = ('post_merge',)

    pkgcore_config_type = None

    def __init__(self, renames_dict):
        triggers.base.__init__(self)
        self.renames = renames_dict

    def trigger(self, engine, install_cset):
        for new_entry, old_entry in self.renames.iteritems():
            try:
                install_cset.remove(new_entry)
            except KeyError:
                continue
            install_cset.add(old_entry)
        self.renames.clear()


class ConfigProtectUninstall(triggers.base):

    required_csets = ('uninstall_existing', 'uninstall')
    _hooks = ('pre_unmerge',)

    pkgcore_config_type = None

    def trigger(self, engine, existing_cset, uninstall_cset):
        protected_restrict = gen_config_protect_filter(engine.offset)

        remove = []
        for x in existing_cset.iterfiles():
            if x.location.endswith("/.keep"):
                continue
            if protected_restrict.match(x.location):
                recorded_ent = uninstall_cset[x]
                try:
                    if not simple_chksum_compare(recorded_ent, x):
                        # chksum differs.  file stays.
                        remove.append(recorded_ent)
                # If a file doesn't exist we don't need to remove it
                except IOError, e:
                    if e.errno not in (errno.ENOENT, errno.ENOTDIR):
                        raise

        for x in remove:
            del uninstall_cset[x]


class preinst_contents_reset(triggers.base):

    required_csets = ('new_cset',)
    priority = 1
    _hooks = ('pre_merge',)

    pkgcore_config_type = None

    def __init__(self, format_op):
        triggers.base.__init__(self)
        self.format_op = format_op

    def trigger(self, engine, cset):
        # wipe, and get data again; ebuild preinst does untrackable
        # modifications to the fs
        cset.clear()
        cs = engine.new._parent.scan_contents(self.format_op.env["D"])
        if engine.offset != '/':
            cs = cs.insert_offset(engine.offset)
        cset.update(cs)
        #cs = map_new_cset_livefs(engine, {'cset':cset}, 'cset')
        #cset.clear()
        #cset.update(cs)


class collision_protect(triggers.base):

    required_csets = {
        const.INSTALL_MODE:('install', 'install_existing'),
        const.REPLACE_MODE:('install', 'install_existing', 'old_cset')
        }

    _hooks = ('sanity_check',)
    _engine_types = triggers.INSTALLING_MODES

    def __init__(self, extra_protects=(), extra_disables=()):
        triggers.base.__init__(self)
        self.extra_protects = extra_protects
        self.extra_disables = extra_disables

    def trigger(self, engine, install, existing, old_cset=()):
        if not existing:
            return

        # for the moment, we just care about files
        colliding = existing.difference(install.iterdirs())

        # filter out daft .keep files.

        # hackish, but it works.
        protected_filter = gen_config_protect_filter(engine.offset,
            self.extra_protects, self.extra_disables).match

        l = []
        for x in colliding:
            if x.location.endswith(".keep"):
                l.append(x)
            elif protected_filter(x.location):
                l.append(x)

        colliding.difference_update(l)
        del l, protected_filter
        if not colliding:
            return

        colliding.difference_update(old_cset)
        if colliding:
            raise errors.BlockModification(self,
                "collision-protect: file(s) already exist: ( %s )" %
                ', '.join(repr(x) for x in sorted(colliding)))

class install_into_symdir_protect(triggers.base):

    required_csets = {
        const.INSTALL_MODE:('install', 'install_existing'),
        const.REPLACE_MODE:('install', 'install_existing', 'old_cset')
        }

    _hooks = ('sanity_check',)
    _engine_types = triggers.INSTALLING_MODES

    def __init__(self, extra_protects=(), extra_disables=()):
        triggers.base.__init__(self)
        self.extra_protects = extra_protects
        self.extra_disables = extra_disables

    def trigger(self, engine, install, existing, old_cset=()):
        return
        if not existing:
            return

        # avoid generator madness
        install_into_symdir = []
        for linkset in [install.iterlinks(), existing.iterlinks()]:
            linkset = list(linkset)
            if linkset:
                for inst_file in install.iterfiles():
                    for sym in linkset:
                        if inst_file.location.startswith(sym.location + '/'):
                            install_into_symdir.append(inst_file)

        if install_into_symdir:
            raise errors.BlockModification(self,
                "file(s) installed into symlinked dir, will break when removing files from the original dir: ( %s )" %
                ', '.join(repr(x) for x in sorted(install_into_symdir)))


class InfoRegen(triggers.InfoRegen):

    _label = "ebuild info regen"

    def register(self, engine):
        # wipe pre-existing info triggers.
        for x in self._hooks:
            if x not in engine.hooks:
                continue
            # yucky, but works.
            wipes = [y for y in engine.hooks[x]
                if y.label == triggers.InfoRegen._label]
            for y in wipes:
                engine.hooks[x].remove(y)
        triggers.InfoRegen.register(self, engine)

    def trigger(self, engine, *args):
        self.engine = engine
        self.path = pjoin(engine.offset, "etc/env.d")
        triggers.InfoRegen.trigger(self, engine, *args)

    @property
    def locations(self):
        collapsed_d = collapse_envd(self.path)[0]
        l = collapsed_d.get("INFOPATH", ())
        if not l:
            return triggers.InfoRegen.locations
        elif isinstance(l, basestring):
            l = l.split()
        return l


class SFPerms(triggers.base):
    required_csets = ('new_cset',)
    _hooks = ('pre_merge',)
    _engine_types = triggers.INSTALLING_MODES

    def trigger(self, engine, cset):
        resets = []
        for x in cset.iterfiles():
            if x.mode & 04000:
                if x.mode & 0044:
                    engine.observer.warn("sfperms: dropping group/world read "
                        "due to SetGID: %r" % x)
                    resets.append(x.change_attributes(mode=x.mode & ~044))
            if x.mode & 02000:
                if x.mode & 0004:
                    engine.observer.warn("sfperms: dropping world read "
                        "due to SetUID: %r" % x)
                    resets.append(x.change_attributes(mode=x.mode & ~004))
        cset.update(resets)


def register_multilib_strict_trigger(domain_settings):
    locations = domain_settings.get("MULTILIB_STRICT_DIRS")
    exempt = domain_settings.get("MULTILIB_STRICT_EXEMPT",
        "(perl5|gcc|gcc-lib)")
    deny_pattern = domain_settings.get("MULTILIB_STRICT_DENY")
    if None in (locations, deny_pattern):
        return
    locations = locations.split()
    if not locations:
        return
    elif len(locations) == 1:
        locations = locations[0]
    else:
        locations = "(%s)" % "|".join(locations)
    limit_pattern = "/%s/(?!%s)" % (locations, exempt)
    while "//" in limit_pattern:
        limit_pattern = limit_pattern.replace("//", "/")
    # this seems semi dodgey, specifically, that it won't catch 'em all
    trig = triggers.BlockFileType(".*%s.*" % deny_pattern, limit_pattern)
    return trig


class FixImageSymlinks(triggers.base):
    required_csets = ('new_cset',)
    _hooks = ('pre_merge',)

    pkgcore_config_type = None

    def __init__(self, format_op):
        triggers.base.__init__(self)
        self.format_op = format_op

    def trigger(self, engine, cset):
        d = self.format_op.env["D"].rstrip("/") + "/"
        l = [x for x in cset.iterlinks() if x.target.startswith(d)]
        if engine.observer:
            o = engine.observer
            for x in l:
                o.warn("correcting %s sym pointing into $D: %s" %
                    (x.location, x.target))
        d_len = len(d)

        # drop the leading ${D}, and force an abspath via '/'
        cset.update(x.change_attributes(target=pjoin('/', x.target[d_len:]))
            for x in l)

def generate_triggers(domain):
    domain_settings = domain.settings
    yield env_update()

    protect = domain_settings.get('CONFIG_PROTECT', ())
    if isinstance(protect, basestring):
        protect = protect.split()
    mask = domain_settings.get('CONFIG_PROTECT_MASK', ())
    if isinstance(protect, basestring):
        protect = protect.split()

    yield ConfigProtectInstall(protect, mask)
    yield ConfigProtectUninstall()

    features = domain_settings.get("FEATURES", ())
    if "collision-protect" in features:
        yield collision_protect(protect, mask)

    if "multilib-strict" in features:
        yield register_multilib_strict_trigger(domain_settings)

    if "sfperms" in features:
        yield SFPerms()

    yield install_into_symdir_protect(protect, mask)
    install_mask = domain_settings.get("INSTALL_MASK", '').split()

    for x in ("man", "info", "doc"):
        if "no%s" % x in features:
            install_mask.append("/usr/share/%s" % x)
    l = []
    for x in install_mask:
        x = x.rstrip("/")
        l.append(values.StrRegex(fnmatch.translate(x)))
        l.append(values.StrRegex(fnmatch.translate("%s/*" % x)))
    install_mask = l

    if install_mask:
        if len(install_mask) == 1:
            install_mask = install_mask[0]
        else:
            install_mask = values.OrRestriction(*install_mask)
        yield triggers.PruneFiles(install_mask.match)
        # note that if this wipes all /usr/share/ entries, should
        # wipe the empty dir.

    yield InfoRegen()
