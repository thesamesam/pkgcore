# Copyright: 2006 Marien Zwart <marienz@gentoo.org>
# License: BSD/GPL2

from pkgcore.config import basics

pkgcore_plugins = {
    'global_config': [{
        'basic': basics.ConfigSectionFromStringDict({
            'class': 'pkgcore.ebuild.formatter.BasicFormatter',
            }),
        'pkgcore': basics.ConfigSectionFromStringDict({
            'class': 'pkgcore.ebuild.formatter.PkgcoreFormatter',
            }),
        'portage': basics.ConfigSectionFromStringDict({
            'class': 'pkgcore.ebuild.formatter.PortageFormatter',
            'default': 'True',
            }),
        'portage-verbose': basics.ConfigSectionFromStringDict({
            'class': 'pkgcore.ebuild.formatter.PortageVerboseFormatter',
            }),
        'paludis': basics.ConfigSectionFromStringDict({
            'class': 'pkgcore.ebuild.formatter.PaludisFormatter',
            }),
    }],
}
