# Copyright 2012-2017 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os.path
import re
import typing as T

from .. import coredata
from ..mesonlib import MachineChoice, MesonException, mlog, version_compare
from ..linkers import LinkerEnvVarsMixin
from .c_function_attributes import C_FUNC_ATTRIBUTES
from .mixins.clike import CLikeCompiler
from .mixins.ccrx import CcrxCompiler
from .mixins.xc16 import Xc16Compiler
from .mixins.c2000 import C2000Compiler
from .mixins.arm import ArmCompiler, ArmclangCompiler
from .mixins.visualstudio import MSVCCompiler, ClangClCompiler
from .mixins.gnu import GnuCompiler
from .mixins.intel import IntelGnuLikeCompiler, IntelVisualStudioLikeCompiler
from .mixins.clang import ClangCompiler
from .mixins.elbrus import ElbrusCompiler
from .mixins.pgi import PGICompiler
from .mixins.emscripten import EmscriptenMixin
from .compilers import (
    gnu_winlibs,
    msvc_winlibs,
    Compiler,
)

if T.TYPE_CHECKING:
    from ..envconfig import MachineInfo


class CCompiler(CLikeCompiler, Compiler):

    @staticmethod
    def attribute_check_func(name):
        try:
            return C_FUNC_ATTRIBUTES[name]
        except KeyError:
            raise MesonException('Unknown function attribute "{}"'.format(name))

    language = 'c'

    def __init__(self, exelist, version, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional[str] = None, **kwargs):
        # If a child ObjC or CPP class has already set it, don't set it ourselves
        Compiler.__init__(self, exelist, version, for_machine, info, **kwargs)
        CLikeCompiler.__init__(self, is_cross, exe_wrapper)

    def get_no_stdinc_args(self):
        return ['-nostdinc']

    def sanity_check(self, work_dir, environment):
        code = 'int main(void) { int class=0; return class; }\n'
        return self.sanity_check_impl(work_dir, environment, 'sanitycheckc.c', code)

    def has_header_symbol(self, hname, symbol, prefix, env, *, extra_args=None, dependencies=None):
        fargs = {'prefix': prefix, 'header': hname, 'symbol': symbol}
        t = '''{prefix}
        #include <{header}>
        int main(void) {{
            /* If it's not defined as a macro, try to use as a symbol */
            #ifndef {symbol}
                {symbol};
            #endif
            return 0;
        }}'''
        return self.compiles(t.format(**fargs), env, extra_args=extra_args,
                             dependencies=dependencies)


class ClangCCompiler(ClangCompiler, CCompiler):

    _C17_VERSION = '>=6.0.0'
    _C18_VERSION = '>=8.0.0'

    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None,
                 defines: T.Optional[T.List[str]] = None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross, info, exe_wrapper, **kwargs)
        ClangCompiler.__init__(self, defines)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self):
        opts = CCompiler.get_options(self)
        c_stds = ['c89', 'c99', 'c11']
        g_stds = ['gnu89', 'gnu99', 'gnu11']
        # https://releases.llvm.org/6.0.0/tools/clang/docs/ReleaseNotes.html
        # https://en.wikipedia.org/wiki/Xcode#Latest_versions
        if version_compare(self.version, self._C17_VERSION):
            c_stds += ['c17']
            g_stds += ['gnu17']
        if version_compare(self.version, self._C18_VERSION):
            c_stds += ['c18']
            g_stds += ['gnu18']
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none'] + c_stds + g_stds,
                'none',
            ),
        })
        if self.info.is_windows() or self.info.is_cygwin():
            opts.update({
                'winlibs': coredata.UserArrayOption(
                    'Standard Win libraries to link against',
                    gnu_winlibs,
                ),
            })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options):
        if self.info.is_windows() or self.info.is_cygwin():
            return options['winlibs'].value[:]
        return []


class AppleClangCCompiler(ClangCCompiler):

    """Handle the differences between Apple Clang and Vanilla Clang.

    Right now we just try to map the Xcode-Clang version to a proper
    Clang version, so that user code can just check the usual Clang
    version. Note, however, that we cheat a little and floor the Clang
    version down, because Apple takes snapshots of LLVM/Clang and also
    modifies them a bit. Their compilers actually support a bit more
    than the version exposed here, but typically less than the fully
    released version.

    Note that the mapping has to be updated for each new major
    Xcode-Clang version!
    """

    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None,
                 defines: T.Optional[T.List[str]] = None, **kwargs):
        ClangCCompiler.__init__(self, exelist, version, for_machine, is_cross, info, exe_wrapper, defines, **kwargs)
        xcode_clang_version = None
        # Check and use passed-through --version output data.
        version_output = kwargs.get('version_output')
        if version_output is not None:
            xcode_clang_version = self.map_xcode_clang_version(version_output)
        # Otherwise, try to use full_version which should contain the same information.
        else:
            full_version = kwargs.get('full_version')
            if full_version is not None:
                xcode_clang_version = self.map_xcode_clang_version(full_version)
        # Note that if nothing is available we just give up and use the provided value.
        if xcode_clang_version is not None:
            self.version = xcode_clang_version

    def map_xcode_clang_version(self, text):
        # Apple patches their Xcode-based clang versions to show an Apple-internal
        # version number, mostly correlated with Xcode itself.
        # We don't want to expose this, though, since it's utterly useless for
        # compiler feature checks.
        # For instance, the clang version as shipped with Xcode 6.2 reports a
        # version number of "6.0". If we use that in compiler feature checks, as
        # in "is this clang 3.6.0 or higher?", the expression would evaluate to
        # to true, even though the compiler is actually based on some version
        # between 3.4.0 and 3.5.0.
        # Also, Apple typically takes snapshots of clang and patches them around,
        # including applying later bug fixes, so we never actually know what
        # compiler release an Xcode tool corresponds to. We'll try to floor the
        # version down, which should be a conservative guess.
        xcode_clang_version = None
        xcode_clang_version_regex = re.compile(r"""
        \([a-zA-Z/]*clang-  # Version number must be enclosed in ([optionally something like tags/Apple/]clang-...)
        (
            \d{3,}          # Three or more digits - major version number
        )                   # One occurrence
        (
            \.\d+           # Period and one or more digits
        ){2,3}              # Two or three occurrences of minor/micro/patch version numbers
        \)                  # Enclosing parenthesis (see above)
        """, re.VERBOSE)
        match = xcode_clang_version_regex.search(text)
        if match:
            xcode_clang_version = match.group(1)
        else:
            # Check for legacy versions.
            # Legacy versions do not include the clang hyphen phrase... and
            # might not even be enclosed in parentheses.
            xcode_clang_version_regex = re.compile(r"""
            \(              # Enclosing parenthesis
            (
                \d{2,3}     # Two or three digits - major version number
            )               # One occurrence
            (
                \.\d+       # Period and one or more digits
            ){,2}           # Zero or up to two occurrences of minor/micro version numbers
            \)              # Enclosing parenthesis
            """, re.VERBOSE)
            match = xcode_clang_version_regex.search(text)
            if match:
                xcode_clang_version = match.group(1)
            else:
                # Even more legacy. No enclosing, just numbers.
                xcode_clang_version_regex = re.compile(r"""
                (
                    \d  # Single digit - major version
                )       # One occurrence
                \.\d{2} # Two digits separated by dots
                $       # End of line
                """, re.VERBOSE)
                match = xcode_clang_version_regex.search(text)
                if match:
                    xcode_clang_version = match.group(1)
        if xcode_clang_version is not None:
            xcode_clang_version_lut = {
                ('1'):          '2.5.0',
                ('60'):         '2.6.0',
                ('70'):         '2.7.0',
                ('77', '137'):  '2.8.0',
                ('163', '211'): '2.9.0',
                ('318', '421'): '3.0.0',
                ('425'):        '3.1.0',
                ('500'):        '3.2.0',
                ('503'):        '3.3.0',
                ('600'):        '3.4.0',
                ('602'):        '3.5.0',
                ('700'):        '3.6.0',
                ('703'):        '3.7.0',
                ('800', '802'): '3.8.0',
                ('900'):        '4.0.0',
                ('902'):        '5.0.2',
                ('1000'):       '6.0.1',
                ('1001'):       '7.0.0',
                ('1100'):       '8.0.0',
            }
            for t in xcode_clang_version_lut.keys():
                if type(t) is tuple:
                    for k in t:
                        if k == xcode_clang_version:
                            xcode_clang_version = xcode_clang_version_lut[t]
                else:
                    if t == xcode_clang_version:
                        xcode_clang_version = xcode_clang_version_lut[t]
        return xcode_clang_version

class EmscriptenCCompiler(EmscriptenMixin, LinkerEnvVarsMixin, ClangCCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        if not is_cross:
            raise MesonException('Emscripten compiler can only be used for cross compilation.')
        ClangCCompiler.__init__(self, exelist=exelist, version=version,
                                for_machine=for_machine, is_cross=is_cross,
                                info=info, exe_wrapper=exe_wrapper, **kwargs)
        self.id = 'emscripten'


class ArmclangCCompiler(ArmclangCompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        ArmclangCompiler.__init__(self)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none', 'c90', 'c99', 'c11', 'gnu90', 'gnu99', 'gnu11'],
                'none',
            ),
        })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options):
        return []


class GnuCCompiler(GnuCompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None,
                 defines=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        GnuCompiler.__init__(self, defines)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self):
        opts = CCompiler.get_options(self)
        c_stds = ['c89', 'c99', 'c11']
        g_stds = ['gnu89', 'gnu99', 'gnu11']
        v = '>=8.0.0'
        if version_compare(self.version, v):
            c_stds += ['c17', 'c18']
            g_stds += ['gnu17', 'gnu18']
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none'] + c_stds + g_stds,
                'none',
            ),
        })
        if self.info.is_windows() or self.info.is_cygwin():
            opts.update({
                'winlibs': coredata.UserArrayOption(
                    'Standard Win libraries to link against',
                    gnu_winlibs,
                ),
            })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options):
        if self.info.is_windows() or self.info.is_cygwin():
            return options['winlibs'].value[:]
        return []

    def get_pch_use_args(self, pch_dir, header):
        return ['-fpch-preprocess', '-include', os.path.basename(header)]


class PGICCompiler(PGICompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        PGICompiler.__init__(self)


class ElbrusCCompiler(GnuCCompiler, ElbrusCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None,
                 defines=None, **kwargs):
        GnuCCompiler.__init__(self, exelist, version, for_machine, is_cross,
                              info, exe_wrapper, defines, **kwargs)
        ElbrusCompiler.__init__(self)

    # It does support some various ISO standards and c/gnu 90, 9x, 1x in addition to those which GNU CC supports.
    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                [
                    'none', 'c89', 'c90', 'c9x', 'c99', 'c1x', 'c11',
                    'gnu89', 'gnu90', 'gnu9x', 'gnu99', 'gnu1x', 'gnu11',
                    'iso9899:2011', 'iso9899:1990', 'iso9899:199409', 'iso9899:1999',
                ],
                'none',
            ),
        })
        return opts

    # Elbrus C compiler does not have lchmod, but there is only linker warning, not compiler error.
    # So we should explicitly fail at this case.
    def has_function(self, funcname, prefix, env, *, extra_args=None, dependencies=None):
        if funcname == 'lchmod':
            return False, False
        else:
            return super().has_function(funcname, prefix, env,
                                        extra_args=extra_args,
                                        dependencies=dependencies)


class IntelCCompiler(IntelGnuLikeCompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        IntelGnuLikeCompiler.__init__(self)
        self.lang_header = 'c-header'
        default_warn_args = ['-Wall', '-w3', '-diag-disable:remark']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra']}

    def get_options(self):
        opts = CCompiler.get_options(self)
        c_stds = ['c89', 'c99']
        g_stds = ['gnu89', 'gnu99']
        if version_compare(self.version, '>=16.0.0'):
            c_stds += ['c11']
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none'] + c_stds + g_stds,
                'none',
            ),
        })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args


class VisualStudioLikeCCompilerMixin:

    """Shared methods that apply to MSVC-like C compilers."""

    def get_options(self):
        opts = super().get_options()
        opts.update({
            'winlibs': coredata.UserArrayOption(
                'Windows libs to link against.',
                msvc_winlibs,
            ),
        })
        return opts

    def get_option_link_args(self, options):
        return options['winlibs'].value[:]


class VisualStudioCCompiler(MSVCCompiler, VisualStudioLikeCCompilerMixin, CCompiler):

    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrap, target: str,
                 **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrap, **kwargs)
        MSVCCompiler.__init__(self, target)


class ClangClCCompiler(ClangClCompiler, VisualStudioLikeCCompilerMixin, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrap, target, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrap, **kwargs)
        ClangClCompiler.__init__(self, target)


class IntelClCCompiler(IntelVisualStudioLikeCompiler, VisualStudioLikeCCompilerMixin, CCompiler):

    """Intel "ICL" compiler abstraction."""

    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrap, target, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrap, **kwargs)
        IntelVisualStudioLikeCompiler.__init__(self, target)

    def get_options(self):
        opts = super().get_options()
        c_stds = ['none', 'c89', 'c99', 'c11']
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                c_stds,
                'none',
            ),
        })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value == 'c89':
            mlog.warning("ICL doesn't explicitly implement c89, setting the standard to 'none', which is close.", once=True)
        elif std.value != 'none':
            args.append('/Qstd:' + std.value)
        return args


class ArmCCompiler(ArmCompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        ArmCompiler.__init__(self)

    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none', 'c90', 'c99'],
                'none',
            ),
        })
        return opts

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value != 'none':
            args.append('--' + std.value)
        return args


class CcrxCCompiler(CcrxCompiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        CcrxCompiler.__init__(self)

    # Override CCompiler.get_always_args
    def get_always_args(self):
        return ['-nologo']

    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({
            'std': coredata.UserComboOption(
                'C language standard to use',
                ['none', 'c89', 'c99'],
                'none',
            ),
        })
        return opts

    def get_no_stdinc_args(self):
        return []

    def get_option_compile_args(self, options):
        args = []
        std = options['std']
        if std.value == 'c89':
            args.append('-lang=c')
        elif std.value == 'c99':
            args.append('-lang=c99')
        return args

    def get_compile_only_args(self):
        return []

    def get_no_optimization_args(self):
        return ['-optimize=0']

    def get_output_args(self, target):
        return ['-output=obj=%s' % target]

    def get_werror_args(self):
        return ['-change_message=error']

    def get_include_args(self, path, is_system):
        if path == '':
            path = '.'
        return ['-include=' + path]


class Xc16CCompiler(Xc16Compiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        Xc16Compiler.__init__(self)

    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({'c_std': coredata.UserComboOption('C language standard to use',
                                                       ['none', 'c89', 'c99', 'gnu89', 'gnu99'],
                                                       'none')})
        return opts

    def get_no_stdinc_args(self):
        return []

    def get_option_compile_args(self, options):
        args = []
        std = options['c_std']
        if std.value != 'none':
            args.append('-ansi')
            args.append('-std=' + std.value)
        return args

    def get_compile_only_args(self):
        return []

    def get_no_optimization_args(self):
        return ['-O0']

    def get_output_args(self, target):
        return ['-o%s' % target]

    def get_werror_args(self):
        return ['-change_message=error']

    def get_include_args(self, path, is_system):
        if path == '':
            path = '.'
        return ['-I' + path]


class C2000CCompiler(C2000Compiler, CCompiler):
    def __init__(self, exelist, version, for_machine: MachineChoice,
                 is_cross, info: 'MachineInfo', exe_wrapper=None, **kwargs):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, **kwargs)
        C2000Compiler.__init__(self)

    # Override CCompiler.get_always_args
    def get_always_args(self):
        return []

    def get_options(self):
        opts = CCompiler.get_options(self)
        opts.update({'c_std': coredata.UserComboOption('C language standard to use',
                                                       ['none', 'c89', 'c99', 'c11'],
                                                       'none')})
        return opts

    def get_no_stdinc_args(self):
        return []

    def get_option_compile_args(self, options):
        args = []
        std = options['c_std']
        if std.value != 'none':
            args.append('--' + std.value)
        return args

    def get_compile_only_args(self):
        return []

    def get_no_optimization_args(self):
        return ['-Ooff']

    def get_output_args(self, target):
        return ['--output_file=%s' % target]

    def get_werror_args(self):
        return ['-change_message=error']

    def get_include_args(self, path, is_system):
        if path == '':
            path = '.'
        return ['--include_path=' + path]
