#!/usr/bin/env python
from optparse import OptionParser

import sfepy
from sfepy.base.base import pause
from sfepy.postprocess import Viewer

usage = """%prog [options] filename"""

help = {
    'is_3d' :
    '3d plot mode',
    'rel_scaling' :
    'relative scaling of glyphs (vector field visualization)' \
    ' [default: %default]',
    'clamping' :
    'glyph clamping mode',
    'layout' :
    'layout for multi-field plots' \
    ' [default: %default]',
    'filename' :
    'view image file name' \
    ' [default: %default]',
}

def main():
    parser = OptionParser(usage=usage, version="%prog " + sfepy.__version__)
    parser.add_option("--3d",
                      action="store_true", dest="is_3d",
                      default=False, help=help['is_3d'])
    parser.add_option("-s", "--scale-glyphs", type='float', metavar='float',
                      action="store", dest="rel_scaling",
                      default=0.05, help=help['rel_scaling'])
    parser.add_option("--clamping",
                      action="store_true", dest="clamping",
                      default=False, help=help['clamping'])
    parser.add_option("--layout",
                      action="store", dest="layout",
                      default='rowcol', help=help['layout'])
    parser.add_option("-o",
                      action="store", dest="filename",
                      default='view.png', help=help['filename'])
    options, args = parser.parse_args()

    if (len(args) == 1):
        filename = args[0]
    else:
        parser.print_help(),
        return

    view = Viewer(filename)
    view(is_3d=options.is_3d, rel_scaling=options.rel_scaling,
         clamping=options.clamping, layout=options.layout,
         fig_filename=options.filename)

if __name__ == '__main__':
    main()
