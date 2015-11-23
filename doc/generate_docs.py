#!/usr/bin/env python

import argparse
import errno
import os
import subprocess
import sys
import textwrap

from snakeoil.dist.generate_man_rsts import ManConverter


def generate_man():
    print('Generating files for man pages')

    try:
        os.mkdir('generated')
    except OSError as e:
        if e.errno == errno.EEXIST:
            return
        raise

    bin_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bin')
    scripts = os.listdir(bin_path)

    # Note that filter-env is specially specified, since the command is installed
    # as 'filter-env', but due to python namespace contraints, it uses a '_'
    # instead.
    generated_man_pages = [
        ('pkgcore.scripts.' + s.replace('-', '_'), s) for s in scripts
    ]

    for module, script in generated_man_pages:
        rst = script + '.rst'
        # generate missing, generic man pages
        if not os.path.isfile(os.path.join('man', rst)):
            with open(os.path.join('generated', rst), 'w') as f:
                f.write(textwrap.dedent("""\
                    {header}
                    {script}
                    {header}

                    .. include:: {script}/main_synopsis.rst
                    .. include:: {script}/main_description.rst
                    .. include:: {script}/main_options.rst
                """.format(header=('=' * len(script)), script=script)))
            os.symlink(os.path.join(os.pardir, 'generated', rst), os.path.join('man', rst))
        os.symlink(os.path.join(os.pardir, 'generated', script), os.path.join('man', script))
        ManConverter.regen_if_needed('generated', module, out_name=script)


def generate_html():
    print('Generating API docs')
    subprocess.call(['sphinx-apidoc', '-Tef', '-o', 'api', '../pkgcore', '../pkgcore/test'])


if __name__ == '__main__':
    sys.path.insert(1, os.path.abspath('..'))

    argparser = argparse.ArgumentParser(description='generate docs')
    argparser.add_argument('--man', action='store_true', help='generate man files')
    argparser.add_argument('--html', action='store_true', help='generate API files')

    opts = argparser.parse_args()

    # if run with no args, build all docs
    if not opts.man and not opts.html:
        opts.man = opts.html = True

    if opts.man:
        generate_man()

    if opts.html:
        generate_html()
