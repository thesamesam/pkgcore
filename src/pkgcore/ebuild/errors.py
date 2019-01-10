# Copyright: 2005 Brian Harring <ferringb@gmail.com>
# License: GPL2/BSD

# "More than one statement on a single line"
# pylint: disable-msg=C0321

"""
atom exceptions
"""

__all__ = ("MalformedAtom", "InvalidVersion", "InvalidCPV", "ParseError")

import textwrap

from pkgcore.exceptions import PkgcoreException
from pkgcore.package import errors


class MalformedAtom(errors.InvalidDependency):

    def __init__(self, atom, err=''):
        err = ': ' + err if err else ''
        self.atom, self.err = atom, err
        errors.InvalidDependency.__init__(self, str(self))

    def __str__(self):
        return f"invalid package atom: '{self.atom}'{self.err}"


class InvalidVersion(errors.InvalidDependency):

    def __init__(self, ver, rev, err=''):
        errors.InvalidDependency.__init__(
            self,
            f"Version restriction ver='{ver}', rev='{rev}', is malformed: error {err}")
        self.ver, self.rev, self.err = ver, rev, err


class InvalidCPV(errors.InvalidPackageName):
    """Raised if an invalid cpv was passed in.

    :ivar args: single-element tuple containing the invalid string.
    :type args: C{tuple}
    """


class ParseError(errors.InvalidDependency):

    def __init__(self, s, token=None, msg=None):
        self.dep_str, self.token, self.msg = s, token, msg

    def __str__(self):
        if self.msg is None:
            str_msg = ''
        else:
            str_msg = f': {self.msg}'

        if self.token is not None:
            return f"{self.dep_str!r} is unparseable{str_msg}\nflagged token- {self.token}"
        else:
            return f"{self.dep_str!r} is unparseable{str_msg}"


class SanityCheckError(PkgcoreException):
    """Generic error for sanity check failures."""

    @property
    def verbose_msg(self):
        return str(self)

    def msg(self, verbosity):
        if verbosity > 0:
            return self.verbose_msg
        else:
            return str(self)


class PkgPretendError(SanityCheckError):
    """The pkg_pretend phase check failed for a package."""

    def __init__(self, pkg, output):
        self.pkg = pkg
        self.output = output

    def __str__(self):
        return f'>>> Failed pkg_pretend: {self.pkg.cpvstr}'

    @property
    def verbose_msg(self):
        return f'{self}\n{self.output}'


class RequiredUseError(SanityCheckError):
    """REQUIRED_USE check for a package failed."""

    def __init__(self, pkg, unmatched, required_use):
        self.unmatched = unmatched
        self.required_use = required_use
        self.pkg = pkg

    def __str__(self):
        return f'>>> Failed REQUIRED_USE check: {self.pkg.cpvstr}'

    @property
    def verbose_msg(self):
        verbose = textwrap.dedent(f"""
            REQUIRED_USE requirement wasn't met
            Failed to match: {self.unmatched}
            from: {self.required_use}
            for USE: {' '.join(self.pkg.use)}
            pkg: {self.pkg.cpvstr}
            """
        )
        return f'{self}\n{verbose.strip()}'
