# Copyright: 2009-2011 Brian Harring <ferringb@gmail.com>
# Copyright: 2006 Marien Zwart <marienz@gentoo.org>
# License: BSD/GPL2


"""Utilities for writing commandline utilities.

pkgcore scripts should use the :obj:`ArgumentParser` subclass here for a
consistent commandline "look and feel" (and it tries to make life a
bit easier too). They will probably want to use :obj:`main` from an C{if
__name__ == '__main__'} block too: it will take care of things like
consistent exception handling.

See dev-notes/commandline.rst for more complete documentation.
"""

__all__ = (
    "FormattingHandler", "main",
)

import argparse
from functools import partial
from importlib import import_module
import logging
import os.path
import sys

from snakeoil import cli, compatibility, formatters, modules
from snakeoil.demandload import demandload

from pkgcore.config import load_config, errors

demandload(
    'inspect',
    'signal',
    'traceback',
    'snakeoil:osutils',
    'snakeoil.errors:walk_exception_chain',
    'snakeoil.lists:iflatten_instance,unstable_unique',
    'snakeoil.version:get_version',
    'pkgcore:operations',
    'pkgcore.config:basics',
    'pkgcore.restrictions:packages,restriction',
    'pkgcore.util:parserestrict,split_negations',
)


class FormattingHandler(logging.Handler):
    """Logging handler printing through a formatter."""

    def __init__(self, formatter):
        logging.Handler.__init__(self)
        # "formatter" clashes with a Handler attribute.
        self.out = formatter

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            color = 'red'
        elif record.levelno >= logging.WARNING:
            color = 'yellow'
        else:
            color = 'cyan'
        first_prefix = (self.out.fg(color), self.out.bold, record.levelname,
                        self.out.reset, ' ', record.name, ': ')
        later_prefix = (len(record.levelname) + len(record.name)) * ' ' + ' : '
        self.out.first_prefix.extend(first_prefix)
        self.out.later_prefix.append(later_prefix)
        try:
            for line in self.format(record).split('\n'):
                self.out.write(line, wrap=True)
        finally:
            self.out.later_prefix.pop()
            for i in xrange(len(first_prefix)):
                self.out.first_prefix.pop()


class ExtendCommaDelimited(argparse._AppendAction):
    """Parse comma-separated values into a list."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = []
        if not self.nargs or self.nargs < 1:
            items.extend(filter(None, values.split(',')))
        else:
            for value in values:
                items.extend(filter(None, value.split(',')))
        setattr(namespace, self.dest, items)


class ExtendCommaDelimitedToggle(argparse._AppendAction):
    """Parse comma-separated enabled and disabled values.

    Disabled values are prefixed with "-" while enabled values are entered as
    is.

    For example, from the sequence "-a,b,c,-d" would result in "a" and "d"
    being registered as disabled while "b" and "c" are enabled.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        disabled, enabled = [], []
        if not self.nargs or self.nargs < 1:
            values = [values]
        for value in values:
            neg, pos = split_negations(filter(None, value.split(',')))
            disabled.extend(neg)
            enabled.extend(pos)
        setattr(namespace, self.dest, (tuple(disabled), tuple(enabled)))


class StoreTarget(argparse._AppendAction):
    """Parse extended package atom syntax and optionally set arguments.

    Various target arguments are supported including the following:

    atom
        An extended atom syntax is supported, see the related section
        in pkgcore(5).

    package set
        Used to define lists of packages, the syntax used for these is
        @pkgset. For example, the @system and @world package sets are
        supported.

    extended globbing
        Globbing package names or atoms allows for use cases such as
        ``'far*'`` (merge every package starting with 'far'),
        ``'dev-python/*::gentoo'`` (merge every package in the dev-python
        category from the gentoo repo), or even '*' (merge everything).
    """

    def __init__(self, sets=True, *args, **kwargs):
        super(StoreTarget, self).__init__(*args, **kwargs)
        self.sets = sets

    def __call__(self, parser, namespace, values, option_string=None):
        if self.sets:
            namespace.sets = []
        if isinstance(values, basestring):
            values = [values]
        for token in values:
            if self.sets and token.startswith('@'):
                namespace.sets.append(token[1:])
            else:
                try:
                    argparse._AppendAction.__call__(
                        self, parser, namespace,
                        (token, parserestrict.parse_match(token)), option_string=option_string)
                except parserestrict.ParseError as e:
                    parser.error(e)
        if getattr(namespace, self.dest) is None:
            setattr(namespace, self.dest, [])


class StoreBool(argparse._StoreAction):
    def __init__(self,
                 option_strings,
                 dest,
                 const=None,
                 default=None,
                 required=False,
                 help=None,
                 metavar='BOOLEAN'):
        super(StoreBool, self).__init__(
            option_strings=option_strings,
            dest=dest,
            const=const,
            default=default,
            type=self.boolean,
            required=required,
            help=help,
            metavar=metavar)

    @staticmethod
    def boolean(value):
        value = value.lower()
        if value in ('y', 'yes', 'true'):
            return True
        elif value in ('n', 'no', 'false'):
            return False
        raise ValueError("value %r must be [y|yes|true|n|no|false]" % (value,))


class Delayed(argparse.Action):

    def __init__(self, option_strings, dest, target=None, priority=0, **kwds):
        if target is None:
            raise ValueError("target must be non None for Delayed")

        self.priority = int(priority)
        self.target = target(option_strings=option_strings, dest=dest, **kwds.copy())
        super(Delayed, self).__init__(
            option_strings=option_strings[:],
            dest=dest, nargs=kwds.get("nargs", None), required=kwds.get("required", None),
            help=kwds.get("help", None), metavar=kwds.get("metavar", None))

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, DelayedParse(
            partial(self.target, parser, namespace, values, option_string),
            self.priority))


CONFIG_ALL_DEFAULT = object()


class EnableDebug(argparse._StoreTrueAction):

    def __call__(self, parser, namespace, values, option_string=None):
        super(EnableDebug, self).__call__(
            parser, namespace, values, option_string=option_string)
        logging.root.setLevel(logging.DEBUG)


class ConfigError(Exception):
    pass


class NoDefaultConfigError(ConfigError):
    pass


class StoreConfigObject(argparse._StoreAction):

    default_priority = 20

    def __init__(self, *args, **kwargs):
        self.priority = int(kwargs.pop("priority", self.default_priority))
        self.config_type = kwargs.pop("config_type", None)
        if self.config_type is None or not isinstance(self.config_type, str):
            raise ValueError("config_type must specified, and be a string")

        if kwargs.pop("get_default", False):
            kwargs["default"] = DelayedValue(
                partial(self.store_default, self.config_type,
                        option_string=kwargs.get('option_strings', [None])[0]),
                self.priority)

        self.store_name = kwargs.pop("store_name", False)
        self.writable = kwargs.pop("writable", None)
        self.target = argparse._StoreAction(*args, **kwargs)

        super(StoreConfigObject, self).__init__(*args, **kwargs)

    @staticmethod
    def _choices(sections):
        """Yield available values for a given option."""
        for k, v in sections.iteritems():
            yield k

    def _load_obj(self, sections, name):
        obj_type = self.metavar if self.metavar is not None else self.config_type
        obj_type = obj_type.lower() + ' ' if obj_type is not None else ''

        try:
            val = sections[name]
        except KeyError:
            choices = ', '.join(self._choices(sections))
            if choices:
                choices = ' (available: %s)' % choices

            raise argparse.ArgumentError(
                self, "couldn't find %s%r%s" %
                (obj_type, name, choices))

        if self.writable and getattr(val, 'frozen', False):
            raise argparse.ArgumentError(
                self, "%s%r is readonly" % (obj_type, name))

        if self.store_name:
            return name, val
        return val

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, DelayedParse(
            partial(self._real_call, parser, namespace, values, option_string),
            self.priority))

    def _get_sections(self, config, namespace):
        return getattr(config, self.config_type)

    def _real_call(self, parser, namespace, values, option_string=None):
        config = getattr(namespace, 'config', None)
        if config is None:
            raise ValueError("no config found.  Internal bug")

        sections = self._get_sections(config, namespace)

        if self.nargs == argparse.ZERO_OR_MORE and values == []:
            values = sections.keys()

        if values is CONFIG_ALL_DEFAULT:
            value = [self._load_obj(sections, x) for x in sections]
        elif isinstance(values, basestring):
            value = self._load_obj(sections, values)
        else:
            value = [self._load_obj(sections, x) for x in values]
        setattr(namespace, self.dest, value)

    @staticmethod
    def store_default(config_type, namespace, attr, option_string=None):
        config = getattr(namespace, 'config', None)
        if config is None:
            raise ConfigError("no config found.  Internal bug, or broken on disk configuration.")
        obj = config.get_default(config_type)
        if obj is None:
            known_objs = sorted(getattr(config, config_type).keys())
            msg = "config error: no default object of type %r found.  " % (config_type,)
            if not option_string:
                msg += "Please fix your configuration."
            else:
                msg += "Please either fix your configuration, or set the %s " \
                    "via the %s option." % (config_type, option_string)
            if known_objs:
                msg += "Known %ss: %s" % (config_type, ', '.join(map(repr, known_objs)))
            raise NoDefaultConfigError(msg)
        setattr(namespace, attr, obj)

    @staticmethod
    def store_all_default(config_type, namespace, attr):
        config = getattr(namespace, 'config', None)
        if config is None:
            raise ValueError("no config found.  Internal bug")
        obj = [(k, v) for k, v in getattr(config, config_type).iteritems()]
        setattr(namespace, attr, obj)

    @classmethod
    def lazy_load_object(cls, config_type, key, priority=None):
        if priority is None:
            priority = cls.default_priority
        return DelayedValue(
            partial(cls._lazy_load_object, config_type, key),
            priority)

    @staticmethod
    def _lazy_load_object(config_type, key, namespace, attr):
        try:
            obj = getattr(namespace.config, config_type)[key]
        except KeyError:
            raise ConfigError(
                "Failed loading object %s of type %s" % (config_type, key))
            raise argparse.ArgumentError(
                self, "couldn't find %s %r" % (self.config_type, name))
        setattr(namespace, attr, obj)


class StoreRepoObject(StoreConfigObject):

    def __init__(self, *args, **kwargs):
        if 'config_type' in kwargs:
            raise ValueError(
                "StoreRepoObject: config_type keyword is redundant: got %s"
                % (kwargs['config_type'],))
        self.raw = kwargs.pop("raw", False)
        self.domain_forced = 'domain' in kwargs
        self.domain = kwargs.pop('domain', 'domain')
        if self.raw:
            kwargs['config_type'] = 'repo_config'
        else:
            kwargs['config_type'] = 'repo'
        self.allow_name_lookup = kwargs.pop("allow_name_lookup", True)
        StoreConfigObject.__init__(self, *args, **kwargs)

    def _get_sections(self, config, namespace):
        domain = None
        if self.domain:
            domain = getattr(namespace, self.domain, None)
            if domain is None and self.domain_forced:
                raise ConfigError(
                    "No domain found, but one was forced for %s; "
                    "internal bug.  NS=%s" % (self, namespace))
        if domain is None:
            return StoreConfigObject._get_sections(self, config, namespace)
        return domain.repos_raw if self.raw else domain.repos_configured_filtered

    @staticmethod
    def _choices(sections):
        """Return an iterable of name: location mappings for available repos.

        If a repo doesn't have a proper location just the name is returned.
        """
        for repo_name, repo in sorted(unstable_unique(sections.iteritems())):
            repo_name = getattr(repo, 'repo_id', repo_name)
            if hasattr(repo, 'location'):
                yield '%s:%s' % (repo_name, repo.location)
            else:
                yield repo_name

    def _load_obj(self, sections, name):
        if not self.allow_name_lookup or name in sections:
            return StoreConfigObject._load_obj(self, sections, name)

        # name wasn't found; search for it.
        for repo_name, repo in sections.iteritems():
            if name in repo.aliases:
                name = repo_name
                break

        return StoreConfigObject._load_obj(self, sections, name)


class DomainFromPath(StoreConfigObject):

    def __init__(self, *args, **kwargs):
        kwargs['config_type'] = 'domain'
        StoreConfigObject.__init__(self, *args, **kwargs)

    def _load_obj(self, sections, requested_path):
        targets = list(find_domains_from_path(sections, requested_path))
        if not targets:
            raise ValueError("couldn't find domain at path %r" % (requested_path,))
        elif len(targets) != 1:
            raise ValueError(
                "multiple domains claim root %r: domains %s" %
                (requested_path, ', '.join(repr(x[0]) for x in targets)))
        return targets[0][1]


def find_domains_from_path(sections, path):
    path = osutils.normpath(osutils.abspath(path))
    for name, domain in sections.iteritems():
        root = getattr(domain, 'root', None)
        if root is None:
            continue
        root = osutils.normpath(osutils.abspath(root))
        if root == path:
            yield name, domain


class DelayedValue(object):

    def __init__(self, invokable, priority):
        self.priority = priority
        if not callable(invokable):
            raise TypeError("invokable must be callable")
        self.invokable = invokable

    def __call__(self, namespace, attr):
        self.invokable(namespace, attr)


class DelayedDefault(DelayedValue):

    @classmethod
    def wipe(cls, attrs, priority):
        if isinstance(attrs, basestring):
            attrs = (attrs,)
        return cls(partial(cls._wipe, attrs), priority)

    @staticmethod
    def _wipe(attrs, namespace, triggering_attr):
        for attr in attrs:
            try:
                delattr(namespace, attr)
            except AttributeError:
                pass
        try:
            delattr(namespace, triggering_attr)
        except AttributeError:
            pass


class DelayedParse(DelayedValue):

    def __init__(self, invokable, priority):
        DelayedValue.__init__(self, invokable, priority)

    def __call__(self, namespace, attr):
        self.invokable()


class BooleanQuery(DelayedValue):

    def __init__(self, attrs, klass_type=None, priority=100, converter=None):
        if klass_type == 'and':
            self.klass = packages.AndRestriction
        elif klass_type == 'or':
            self.klass = packages.OrRestriction
        elif callable(klass_type):
            self.klass = klass
        else:
            raise ValueError(
                "klass_type either needs to be 'or', 'and', "
                "or a callable.  Got %r" % (klass_type,))

        if converter is not None and not callable(converter):
            raise ValueError(
                "converter either needs to be None, or a callable;"
                " got %r" % (converter,))

        self.converter = converter
        self.priority = int(priority)
        self.attrs = tuple(attrs)

    def invokable(self, namespace, attr):
        l = []
        for x in self.attrs:
            val = getattr(namespace, x, None)
            if val is None:
                continue
            if isinstance(val, restriction.base):
                l.append(val)
            else:
                l.extend(val)

        if self.converter:
            l = self.converter(l, namespace)

        l = list(iflatten_instance(l, (restriction.base,)))

        if len(l) > 1:
            val = self.klass(*l)
        elif l:
            val = l[0]
        else:
            val = None
        setattr(namespace, attr, val)


def make_query(parser, *args, **kwargs):
    klass_type = kwargs.pop("klass_type", "or")
    dest = kwargs.pop("dest", None)
    if dest is None:
        raise TypeError("dest must be specified via kwargs")
    attrs = kwargs.pop("attrs", [])
    subattr = "_%s" % (dest,)
    kwargs["dest"] = subattr
    if kwargs.get('type', False) is None:
        del kwargs['type']
    else:
        def query(value):
            return parserestrict.parse_match(value)
        kwargs.setdefault("type", query)
    kwargs.setdefault("metavar", dest)
    final_priority = kwargs.pop("final_priority", None)
    final_converter = kwargs.pop("final_converter", None)
    parser.add_argument(*args, **kwargs)
    bool_kwargs = {'converter': final_converter}
    if final_priority is not None:
        bool_kwargs['priority'] = final_priority
    obj = BooleanQuery(list(attrs) + [subattr], klass_type=klass_type, **bool_kwargs)
    # note that dict expansion has to be used here; dest=obj would just set a
    # default named 'dest'
    parser.set_defaults(**{dest: obj})


class Expansion(argparse.Action):

    def __init__(self, option_strings, dest, nargs=None, help=None,
                 required=None, subst=None):
        if subst is None:
            raise TypeError("resultant_string must be set")

        super(Expansion, self).__init__(
            option_strings=option_strings,
            dest=dest,
            help=help,
            required=required,
            default=False,
            nargs=nargs)
        self.subst = tuple(subst)

    def __call__(self, parser, namespace, values, option_string=None):
        actions = parser._actions
        action_map = {}
        vals = values
        if isinstance(values, basestring):
            vals = [vals]
        dvals = {str(idx): val for idx, val in enumerate(vals)}
        dvals['*'] = ' '.join(vals)

        for action in actions:
            action_map.update((option, action) for option in action.option_strings)

        for chunk in self.subst:
            option, args = chunk[0], chunk[1:]
            action = action_map.get(option)
            args = [x % dvals for x in args]
            if not action:
                raise ValueError(
                    "unable to find option %r for %r" %
                    (option, self.option_strings))
            if action.type is not None:
                args = map(action.type, args)
            if action.nargs in (1, None):
                args = args[0]
            action(parser, namespace, args, option_string=option_string)
        setattr(namespace, self.dest, True)


def python_namespace_type(value, module=False, attribute=False):
    """
    return the object from python namespace that value specifies

    :param value: python namespace, snakeoil.modules for example
    :param module: if true, the object must be a module
    :param attribute: if true, the object must be a non-module
    :raises ValueError: if the conditions aren't met, or import fails
    """
    try:
        if module:
            return import_module(value)
        elif attribute:
            return modules.load_attribute(value)
        return modules.load_any(value)
    except (ImportError, modules.FailedImport) as err:
        compatibility.raise_from(argparse.ArgumentTypeError(str(err)))


class _SubParser(argparse._SubParsersAction):

    def add_parser(self, name, **kwds):
        """argparser subparser that links description/help if one is specified"""
        description = kwds.get("description")
        help_txt = kwds.get("help")
        if description is None:
            if help_txt is not None:
                kwds["description"] = help_txt
        elif help_txt is None:
            kwds["help"] = description
        return argparse._SubParsersAction.add_parser(self, name, **kwds)

    def __call__(self, parser, namespace, values, option_string=None):
        """override stdlib argparse to revert subparser namespace changes

        Reverts the broken upstream change made in issue #9351 which causes
        issue #23058. This can be dropped when the problem is fixed upstream.
        """
        parser_name = values[0]
        arg_strings = values[1:]

        # set the parser name if requested
        if self.dest is not argparse.SUPPRESS:
            setattr(namespace, self.dest, parser_name)

        # select the parser
        try:
            parser = self._name_parser_map[parser_name]
        except KeyError:
            tup = parser_name, ', '.join(self._name_parser_map)
            msg = _('unknown parser %r (choices: %s)') % tup
            raise ArgumentError(self, msg)

        # parse all the remaining options into the namespace
        # store any unrecognized options on the object, so that the top
        # level parser can decide what to do with them
        namespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
        if arg_strings:
            vars(namespace).setdefault(argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)


class ArgumentParser(argparse.ArgumentParser):

    def __init__(self,
                 prog=None,
                 usage=None,
                 description=None,
                 docs=None,
                 epilog=None,
                 parents=[],
                 formatter_class=argparse.HelpFormatter,
                 prefix_chars='-',
                 fromfile_prefix_chars=None,
                 argument_default=None,
                 conflict_handler='error',
                 add_help=True):

        if description is not None:
            description_lines = description.split('\n', 1)
            description = description_lines[0]
            if docs is None and len(description_lines) == 2:
                docs = description_lines[1]

        self.docs = docs

        super(ArgumentParser, self).__init__(
            prog=prog, usage=usage,
            description=description, epilog=epilog,
            parents=parents, formatter_class=formatter_class,
            prefix_chars=prefix_chars, fromfile_prefix_chars=fromfile_prefix_chars,
            argument_default=argument_default, conflict_handler=conflict_handler,
            add_help=add_help)
        # register our own subparser
        self.register('action', 'parsers', _SubParser)

    def parse_args(self, args=None, namespace=None):
        args = argparse.ArgumentParser.parse_args(self, args, namespace)

        # two runs are required; first, handle any suppression defaults
        # introduced.  subparsers defaults cannot override the parent parser,
        # as such a subparser can't turn off config/domain for example.
        # so we first find all DelayedDefault
        # run them, then rescan for delayeds to run.
        # this allows subparsers to introduce a default named for themselves
        # that suppresses the parent.

        # intentionally no protection of suppression code; this should
        # just work.

        i = ((attr, val) for attr, val in args.__dict__.iteritems()
             if isinstance(val, DelayedDefault))
        for attr, functor in sorted(i, key=lambda val: val[1].priority):
            functor(args, attr)

        # now run the delays.
        i = ((attr, val) for attr, val in args.__dict__.iteritems()
             if isinstance(val, DelayedValue))
        try:
            for attr, delayed in sorted(i, key=lambda val: val[1].priority):
                delayed(args, attr)
        except (TypeError, ValueError) as err:
            self.error("failed loading/parsing %s: %s" % (attr, str(err)))
        except (ConfigError, argparse.ArgumentError):
            err = sys.exc_info()[1]
            self.error(str(err))

        final_check = getattr(args, 'final_check', None)
        if final_check is not None:
            del args.final_check
            final_check(self, args)
        return args

    def error(self, message):
        """Print an error message and exit.

        Similar to argparse's error() except usage information is not shown by
        default.
        """
        self.exit(2, '%s: error: %s\n' % (self.prog, message))

    def bind_main_func(self, functor):
        self.set_defaults(main_func=functor)
        # override main prog with subcmd prog
        self.set_defaults(prog=self.prog)
        return functor

    def bind_class(self, obj):
        if not isinstance(obj, ArgparseCommand):
            raise ValueError(
                "expected obj to be an instance of "
                "ArgparseCommand; got %r" % (obj,))
        obj.bind_to_parser(self)
        return self

    def bind_delayed_default(self, priority, name=None):
        def f(functor, name=name):
            if name is None:
                name = functor.__name__
            self.set_defaults(**{name: DelayedValue(functor, priority)})
            return functor
        return f

    def add_subparsers(self, **kwargs):
        kwargs.setdefault('title', 'subcommands')
        kwargs.setdefault('dest', 'subcommand')
        subparsers = argparse.ArgumentParser.add_subparsers(self, **kwargs)
        subparsers.required = True
        return subparsers

    def bind_final_check(self, functor):
        self.set_defaults(final_check=functor)
        return functor


class ArgparseCommand(object):

    def bind_to_parser(self, parser):
        parser.bind_main_func(self)

    def __call__(self, namespace, out, err):
        raise NotImplementedError(self, '__call__')


def register_command(commands, real_type=type):
    def f(name, bases, scope, real_type=real_type, commands=commands):
        o = real_type(name, bases, scope)
        commands.append(o)
        return o
    return f


def _convert_config_mods(iterable):
    d = {}
    if iterable is None:
        return d
    for (section, key, value) in iterable:
        d.setdefault(section, {})[key] = value
    return d


def store_config(namespace, attr):
    configs = map(
        _convert_config_mods, [namespace.new_config, namespace.add_config])
    # add necessary inherits for add_config
    for key, vals in configs[1].iteritems():
        vals.setdefault('inherit', key)

    configs = [{section: basics.ConfigSectionFromStringDict(vals)
                for section, vals in d.iteritems()}
               for d in configs if d]

    config = load_config(
        skip_config_files=namespace.empty_config,
        append_sources=tuple(configs),
        location=namespace.override_config,
        **vars(namespace))
    setattr(namespace, attr, config)


def _mk_domain(parser):
    parser.add_argument(
        '--domain', get_default=True, config_type='domain',
        action=StoreConfigObject,
        help="domain to use for this operation")


def existent_path(value):
    if not os.path.exists(value):
        raise ValueError("path %r doesn't exist on disk" % (value,))
    try:
        return osutils.abspath(value)
    except EnvironmentError as e:
        compatibility.raise_from(
            ValueError(
                "while resolving path %r, encountered error: %r" %
                (value, e)))


def mk_argparser(suppress=False, config=True, domain=True,
                 color=True, debug=True, quiet=True, verbose=True,
                 version=True, **kwds):
    p = ArgumentParser(**kwds)
    p.register('action', 'extend_comma', ExtendCommaDelimited)
    p.register('action', 'extend_comma_toggle', ExtendCommaDelimitedToggle)

    if suppress:
        return p

    if version:
        # Get the calling script's module and project names, this assumes a
        # project layout similar to pkgcore's where scripts are located in the
        # project.scripts.script namespace.
        script = inspect.stack(0)[1][0].f_globals['__file__']
        project = script.split(os.path.sep)[-3]
        p.add_argument(
            '--version', action='version', version=get_version(project, script),
            docs="Show this program's version number and exit.")
    if debug:
        p.add_argument(
            '--debug', action=EnableDebug, help='enable debugging checks',
            docs='Enable debug checks and show verbose debug output.')
    if quiet:
        p.add_argument(
            '-q', '--quiet', action='store_true',
            help='suppress non-error messages',
            docs="Suppress non-error, informational messages.")
    if verbose:
        p.add_argument(
            '-v', '--verbose', action='count',
            help='show verbose output',
            docs="Increase the verbosity of various output.")
    if color:
        p.add_argument(
            '--color', action=StoreBool,
            default=sys.stdout.isatty(),
            help='enable/disable color support',
            docs="""
                Toggle colored output support. This can be used to forcibly
                enable color support when piping output or other sitations
                where stdout is not a tty.
            """)

    if config:
        p.add_argument(
            '--add-config', nargs=3, action='append',
            metavar=('SECTION', 'KEY', 'VALUE'),
            help='modify an existing configuration section')
        p.add_argument(
            '--new-config', nargs=3, action='append',
            metavar=('SECTION', 'KEY', 'VALUE'),
            help='add a new configuration section')
        p.add_argument(
            '--empty-config', action='store_true', default=False,
            help='do not load user/system configuration')
        p.add_argument(
            '--config', metavar='PATH', dest='override_config',
            type=existent_path,
            help='override location of config files')

        p.set_defaults(config=DelayedValue(store_config, 0))

    if domain:
        _mk_domain(p)
    return p


def argparse_parse(parser, args, namespace=None):
    namespace = parser.parse_args(args, namespace=namespace)
    main = getattr(namespace, 'main_func', None)
    if main is None:
        raise Exception(
            "parser %r lacks a main method- internal bug.\nGot namespace %r\n"
            % (parser, namespace))
    return main, namespace


def convert_to_restrict(sequence, default=packages.AlwaysTrue):
    """Convert an iterable to a list of atoms, or return the default"""
    l = []
    try:
        for x in sequence:
            l.append(parserestrict.parse_match(x))
    except parserestrict.ParseError as e:
        compatibility.raise_from(
            argparse.ArgumentError(
                "arg %r isn't a valid atom: %s" % (x, e)))
    return l or [default]


def main(parser, args=None, outfile=None, errfile=None):
    """Function to use in an "if __name__ == '__main__'" block in a script.

    Takes an argparser instance and runs it against available args, them,
    taking care of exception handling and some other things.

    Any ConfigurationErrors raised from your function (by the config
    manager) are handled. Other exceptions are not (trigger a traceback).

    :type parser: ArgumentParser instance
    :param parser: Argument parser for external commands or scripts.
    :type args: sequence of strings
    :param args: arguments to parse, defaulting to C{sys.argv[1:]}.
    :type outfile: file-like object
    :param outfile: File to use for stdout, defaults to C{sys.stdout}.
    :type errfile: file-like object
    :param errfile: File to use for stderr, defaults to C{sys.stderr}.
    """
    exitstatus = 1

    if outfile is None:
        outfile = sys.stdout
    if errfile is None:
        errfile = sys.stderr

    out_fd = err_fd = None
    if hasattr(outfile, 'fileno') and hasattr(errfile, 'fileno'):
        if compatibility.is_py3k:
            # annoyingly, fileno can exist but through unsupport
            import io
            try:
                out_fd, err_fd = outfile.fileno(), errfile.fileno()
            except (io.UnsupportedOperation, IOError):
                pass
        else:
            try:
                out_fd, err_fd = outfile.fileno(), errfile.fileno()
            except IOError:
                # shouldn't be possible, but docs claim it, thus protect.
                pass

    if out_fd is not None and err_fd is not None:
        out_stat, err_stat = os.fstat(out_fd), os.fstat(err_fd)
        if out_stat.st_dev == err_stat.st_dev \
                and out_stat.st_ino == err_stat.st_ino and \
                not errfile.isatty():
            # they're the same underlying fd.  thus
            # point the handles at the same so we don't
            # get intermixed buffering issues.
            errfile = outfile

    out = options = None
    exitstatus = -10
    # can't use options.debug since argparsing might fail
    debug = '--debug' in sys.argv[1:]
    try:
        main_func, options = argparse_parse(parser, args, options)

        if debug:
            # verbosity level affects debug output
            verbose = getattr(options, 'verbose', None)
            debug_verbosity = verbose if verbose is not None else 1
            # pass down debug setting to the bash side
            os.environ['PKGCORE_DEBUG'] = str(debug_verbosity)

        if getattr(options, 'color', True):
            formatter_factory = partial(
                formatters.get_formatter, force_color=getattr(options, 'color', False))
        else:
            formatter_factory = formatters.PlainTextFormatter
            # pass down color setting to the bash side
            if 'PKGCORE_NOCOLOR' not in os.environ:
                os.environ['PKGCORE_NOCOLOR'] = '1'

        out = formatter_factory(outfile)
        err = formatter_factory(errfile)
        if logging.root.handlers:
            # Remove the default handler.
            logging.root.handlers.pop(0)
        logging.root.addHandler(FormattingHandler(err))
        exitstatus = main_func(options, out, err)
    except KeyboardInterrupt:
        errfile.write("keyboard interrupted- exiting")
        if debug:
            traceback.print_tb(sys.exc_info()[-1])
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.killpg(os.getpgid(0), signal.SIGINT)
    except SystemExit:
        # use our own exit status
        pass
    except compatibility.IGNORED_EXCEPTIONS:
        raise
    except errors.ParsingError as e:
        if debug:
            tb = sys.exc_info()[-1]
            dump_error(e, 'Error while parsing arguments', tb=tb)
        else:
            parser.error(e)
    except errors.ConfigurationError as e:
        tb = sys.exc_info()[-1]
        if not debug:
            tb = None
        dump_error(e, "Error in configuration", handle=errfile, tb=tb)
    except operations.OperationError as e:
        tb = sys.exc_info()[-1]
        if not debug:
            tb = None
        dump_error(e, "Error running an operation", handle=errfile, tb=tb)
    except Exception as e:
        # force tracebacks for unhandled exceptions
        tb = sys.exc_info()[-1]
        dump_error(e, "Unhandled exception occurred", handle=errfile, tb=tb)
    if out is not None:
        if exitstatus:
            out.title('%s failed' % (options.prog,))
        else:
            out.title('%s succeeded' % (options.prog,))
    raise SystemExit(exitstatus)


def dump_error(raw_exc, msg=None, handle=sys.stderr, tb=None):
    # force default output for exceptions
    if getattr(handle, 'reset', False):
        handle.write(handle.reset)

    prefix = ''
    if msg:
        prefix = ' '
        handle.write(msg.rstrip("\n") + ":\n")
        if tb:
            handle.write("Traceback follows:\n")
            traceback.print_tb(tb, file=handle)
    exc_strings = []
    if raw_exc is not None:
        for exc in walk_exception_chain(raw_exc):
            exc_strings.extend(
                '%s%s' % (prefix, x.strip())
                for x in filter(None, str(exc).split("\n")))
    if exc_strings:
        if msg and tb:
            handle.write("\n%s:\n" % raw_exc.__class__.__name__)
        handle.write("\n".join(exc_strings))
        handle.write("\n")
