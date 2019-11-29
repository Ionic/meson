# Copyright 2012-2020 The Meson development team

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
from ..mesonlib import MachineChoice, MesonException, mlog, version_compare, OptionKey
from .c_function_attributes import C_FUNC_ATTRIBUTES
from .mixins.clike import CLikeCompiler
from .mixins.ccrx import CcrxCompiler
from .mixins.xc16 import Xc16Compiler
from .mixins.compcert import CompCertCompiler
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
    from ..coredata import KeyedOptionDictType
    from ..dependencies import Dependency, ExternalProgram
    from ..envconfig import MachineInfo
    from ..environment import Environment
    from ..linkers import DynamicLinker

    CompilerMixinBase = Compiler
else:
    CompilerMixinBase = object



class CCompiler(CLikeCompiler, Compiler):

    @staticmethod
    def attribute_check_func(name: str) -> str:
        try:
            return C_FUNC_ATTRIBUTES[name]
        except KeyError:
            raise MesonException('Unknown function attribute "{}"'.format(name))

    language = 'c'

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        # If a child ObjC or CPP class has already set it, don't set it ourselves
        Compiler.__init__(self, exelist, version, for_machine, info,
                          is_cross=is_cross, full_version=full_version, linker=linker)
        CLikeCompiler.__init__(self, exe_wrapper)

    def get_no_stdinc_args(self) -> T.List[str]:
        return ['-nostdinc']

    def sanity_check(self, work_dir: str, environment: 'Environment') -> None:
        code = 'int main(void) { int class=0; return class; }\n'
        return self._sanity_check_impl(work_dir, environment, 'sanitycheckc.c', code)

    def has_header_symbol(self, hname: str, symbol: str, prefix: str,
                          env: 'Environment', *,
                          extra_args: T.Optional[T.List[str]] = None,
                          dependencies: T.Optional[T.List['Dependency']] = None) -> T.Tuple[bool, bool]:
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

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
        opts.update({
            OptionKey('std', machine=self.for_machine, lang=self.language): coredata.UserComboOption(
                'C langauge standard to use',
                ['none'],
                'none',
            )
        })
        return opts


class _ClangCStds(CompilerMixinBase):

    """Mixin class for clang based compilers for setting C standards.

    This is used by both ClangCCompiler and ClangClCompiler, as they share
    the same versions
    """

    _C17_VERSION = '>=6.0.0'
    _C18_VERSION = '>=8.0.0'
    _C2X_VERSION = '>=9.0.0'

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
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
        if version_compare(self.version, self._C2X_VERSION):
            c_stds += ['c2x']
            g_stds += ['gnu2x']
        opts[OptionKey('std', machine=self.for_machine, lang=self.language)].choices = ['none'] + c_stds + g_stds
        return opts


class ClangCCompiler(_ClangCStds, ClangCompiler, CCompiler):

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 defines: T.Optional[T.Dict[str, str]] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross, info, exe_wrapper, linker=linker, full_version=full_version)
        ClangCompiler.__init__(self, defines)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
        if self.info.is_windows() or self.info.is_cygwin():
            opts.update({
                OptionKey('winlibs', machine=self.for_machine, lang=self.language): coredata.UserArrayOption(
                    'Standard Win libraries to link against',
                    gnu_winlibs,
                ),
            })
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        std = options[OptionKey('std', machine=self.for_machine, lang=self.language)]
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        if self.info.is_windows() or self.info.is_cygwin():
            # without a typedict mypy can't understand this.
            libs = options[OptionKey('winlibs', machine=self.for_machine, lang=self.language)].value.copy()
            assert isinstance(libs, list)
            for l in libs:
                assert isinstance(l, str)
            return libs
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

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 defines: T.Optional[T.Dict[str, str]] = None,
                 full_version: T.Optional[str] = None):
        ClangCCompiler.__init__(self, exelist, version, for_machine, is_cross, info, exe_wrapper, linker, defines, full_version)
        xcode_clang_version = None
        # Check and use passed-through full version output data.
        # This only includes the first line, but should contain enough information
        # for our use case.
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
                ('1'):           '2.5.0',
                ('60'):          '2.6.0',
                ('70'):          '2.7.0',
                ('77', '137'):   '2.8.0',
                ('163', '211'):  '2.9.0',
                ('318', '421'):  '3.0.0',
                ('425'):         '3.1.0',
                ('500'):         '3.2.0',
                ('503'):         '3.3.0',
                ('600'):         '3.4.0',
                ('602'):         '3.5.0',
                ('700'):         '3.6.0',
                ('703'):         '3.7.0',
                ('800', '802'):  '3.8.0',
                ('900'):         '4.0.0',
                ('902'):         '5.0.2',
                ('1000'):        '6.0.1',
                ('1001'):        '7.0.0',
                ('1100'):        '8.0.0',
                ('1103'):        '9.0.0',
                ('1200'):       '10.0.0',
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

class EmscriptenCCompiler(EmscriptenMixin, ClangCCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 defines: T.Optional[T.Dict[str, str]] = None,
                 full_version: T.Optional[str] = None):
        if not is_cross:
            raise MesonException('Emscripten compiler can only be used for cross compilation.')
        ClangCCompiler.__init__(self, exelist, version, for_machine, is_cross,
                                info, exe_wrapper=exe_wrapper, linker=linker,
                                defines=defines, full_version=full_version)
        self.id = 'emscripten'


class ArmclangCCompiler(ArmclangCompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        ArmclangCompiler.__init__(self)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c90', 'c99', 'c11', 'gnu90', 'gnu99', 'gnu11']
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        std = options[OptionKey('std', machine=self.for_machine, lang=self.language)]
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        return []


class GnuCCompiler(GnuCompiler, CCompiler):

    _C18_VERSION = '>=8.0.0'
    _C2X_VERSION = '>=9.0.0'

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 defines: T.Optional[T.Dict[str, str]] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross, info, exe_wrapper, linker=linker, full_version=full_version)
        GnuCompiler.__init__(self, defines)
        default_warn_args = ['-Wall', '-Winvalid-pch']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra', '-Wpedantic']}

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        c_stds = ['c89', 'c99', 'c11']
        g_stds = ['gnu89', 'gnu99', 'gnu11']
        if version_compare(self.version, self._C18_VERSION):
            c_stds += ['c17', 'c18']
            g_stds += ['gnu17', 'gnu18']
        if version_compare(self.version, self._C2X_VERSION):
            c_stds += ['c2x']
            g_stds += ['gnu2x']
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none'] + c_stds + g_stds
        if self.info.is_windows() or self.info.is_cygwin():
            opts.update({
                key.evolve('winlibs'): coredata.UserArrayOption(
                    'Standard Win libraries to link against',
                    gnu_winlibs,
                ),
            })
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        std = options[OptionKey('std', lang=self.language, machine=self.for_machine)]
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args

    def get_option_link_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        if self.info.is_windows() or self.info.is_cygwin():
            # without a typeddict mypy can't figure this out
            libs: T.List[str] = options[OptionKey('winlibs', lang=self.language, machine=self.for_machine)].value.copy()
            assert isinstance(libs, list)
            for l in libs:
                assert isinstance(l, str)
            return libs
        return []

    def get_pch_use_args(self, pch_dir: str, header: str) -> T.List[str]:
        return ['-fpch-preprocess', '-include', os.path.basename(header)]


class PGICCompiler(PGICompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        PGICompiler.__init__(self)


class NvidiaHPC_CCompiler(PGICompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        PGICompiler.__init__(self)
        self.id = 'nvidia_hpc'


class ElbrusCCompiler(GnuCCompiler, ElbrusCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 defines: T.Optional[T.Dict[str, str]] = None,
                 full_version: T.Optional[str] = None):
        GnuCCompiler.__init__(self, exelist, version, for_machine, is_cross,
                              info, exe_wrapper, defines=defines,
                              linker=linker, full_version=full_version)
        ElbrusCompiler.__init__(self)

    # It does support some various ISO standards and c/gnu 90, 9x, 1x in addition to those which GNU CC supports.
    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        opts[OptionKey('std', machine=self.for_machine, lang=self.language)].choices = [
            'none', 'c89', 'c90', 'c9x', 'c99', 'c1x', 'c11',
            'gnu89', 'gnu90', 'gnu9x', 'gnu99', 'gnu1x', 'gnu11',
            'iso9899:2011', 'iso9899:1990', 'iso9899:199409', 'iso9899:1999',
        ]
        return opts

    # Elbrus C compiler does not have lchmod, but there is only linker warning, not compiler error.
    # So we should explicitly fail at this case.
    def has_function(self, funcname: str, prefix: str, env: 'Environment', *,
                     extra_args: T.Optional[T.List[str]] = None,
                     dependencies: T.Optional[T.List['Dependency']] = None) -> T.Tuple[bool, bool]:
        if funcname == 'lchmod':
            return False, False
        else:
            return super().has_function(funcname, prefix, env,
                                        extra_args=extra_args,
                                        dependencies=dependencies)


class IntelCCompiler(IntelGnuLikeCompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice, is_cross: bool,
                 info: 'MachineInfo', exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        IntelGnuLikeCompiler.__init__(self)
        self.lang_header = 'c-header'
        default_warn_args = ['-Wall', '-w3', '-diag-disable:remark']
        self.warn_args = {'0': [],
                          '1': default_warn_args,
                          '2': default_warn_args + ['-Wextra'],
                          '3': default_warn_args + ['-Wextra']}

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        c_stds = ['c89', 'c99']
        g_stds = ['gnu89', 'gnu99']
        if version_compare(self.version, '>=16.0.0'):
            c_stds += ['c11']
        opts[OptionKey('std', machine=self.for_machine, lang=self.language)].choices = ['none'] + c_stds + g_stds
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        std = options[OptionKey('std', machine=self.for_machine, lang=self.language)]
        if std.value != 'none':
            args.append('-std=' + std.value)
        return args


class VisualStudioLikeCCompilerMixin(CompilerMixinBase):

    """Shared methods that apply to MSVC-like C compilers."""

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
        opts.update({
            OptionKey('winlibs', machine=self.for_machine, lang=self.language): coredata.UserArrayOption(
                'Windows libs to link against.',
                msvc_winlibs,
            ),
        })
        return opts

    def get_option_link_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        # need a TypeDict to make this work
        key = OptionKey('winlibs', machine=self.for_machine, lang=self.language)
        libs = options[key].value.copy()
        assert isinstance(libs, list)
        for l in libs:
            assert isinstance(l, str)
        return libs


class VisualStudioCCompiler(MSVCCompiler, VisualStudioLikeCCompilerMixin, CCompiler):

    _C11_VERSION = '>=19.28'
    _C17_VERSION = '>=19.28'

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo', target: str,
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker,
                           full_version=full_version)
        MSVCCompiler.__init__(self, target)

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
        c_stds = ['c89', 'c99']
                  # Need to have these to be compatible with projects
                  # that set c_std to e.g. gnu99.
                  # https://github.com/mesonbuild/meson/issues/7611
        g_stds = ['gnu89', 'gnu90', 'gnu9x', 'gnu99']
        if version_compare(self.version, self._C11_VERSION):
            c_stds += ['c11']
            g_stds += ['gnu1x', 'gnu11']
        if version_compare(self.version, self._C17_VERSION):
            c_stds += ['c17', 'c18']
            g_stds += ['gnu17', 'gnu18']
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none'] + c_stds + g_stds
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        std = options[OptionKey('std', machine=self.for_machine, lang=self.language)]
        if std.value.startswith('gnu'):
            mlog.log_once(
                'cl.exe does not actually support gnu standards, and meson '
                'will instead demote to the nearest ISO C standard. This '
                'may cause compilation to fail.')
        # As of MVSC 16.8, /std:c11 and /std:c17 are the only valid C standard options.
        if std.value in {'c11', 'gnu1x', 'gnu11'}:
            args.append('/std:c11')
        elif std.value in {'c17', 'c18', 'gnu17', 'gnu18'}:
            args.append('/std:c17')
        return args


class ClangClCCompiler(_ClangCStds, ClangClCompiler, VisualStudioLikeCCompilerMixin, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo', target: str,
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker,
                           full_version=full_version)
        ClangClCompiler.__init__(self, target)

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key].value
        if std != "none":
            return ['/clang:-std={}'.format(std)]
        return []


class IntelClCCompiler(IntelVisualStudioLikeCompiler, VisualStudioLikeCCompilerMixin, CCompiler):

    """Intel "ICL" compiler abstraction."""

    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo', target: str,
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker,
                           full_version=full_version)
        IntelVisualStudioLikeCompiler.__init__(self, target)

    def get_options(self) -> 'KeyedOptionDictType':
        opts = super().get_options()
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99', 'c11']
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key]
        if std.value == 'c89':
            mlog.log_once("ICL doesn't explicitly implement c89, setting the standard to 'none', which is close.")
        elif std.value != 'none':
            args.append('/Qstd:' + std.value)
        return args


class ArmCCompiler(ArmCompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo',
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker,
                           full_version=full_version)
        ArmCompiler.__init__(self)

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99', 'c11']
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key]
        if std.value != 'none':
            args.append('--' + std.value)
        return args


class CcrxCCompiler(CcrxCompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo',
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        CcrxCompiler.__init__(self)

    # Override CCompiler.get_always_args
    def get_always_args(self) -> T.List[str]:
        return ['-nologo']

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99']
        return opts

    def get_no_stdinc_args(self) -> T.List[str]:
        return []

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key]
        if std.value == 'c89':
            args.append('-lang=c')
        elif std.value == 'c99':
            args.append('-lang=c99')
        return args

    def get_compile_only_args(self) -> T.List[str]:
        return []

    def get_no_optimization_args(self) -> T.List[str]:
        return ['-optimize=0']

    def get_output_args(self, target: str) -> T.List[str]:
        return ['-output=obj=%s' % target]

    def get_werror_args(self) -> T.List[str]:
        return ['-change_message=error']

    def get_include_args(self, path: str, is_system: bool) -> T.List[str]:
        if path == '':
            path = '.'
        return ['-include=' + path]


class Xc16CCompiler(Xc16Compiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo',
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        Xc16Compiler.__init__(self)

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99', 'gnu89', 'gnu99']
        return opts

    def get_no_stdinc_args(self) -> T.List[str]:
        return []

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key]
        if std.value != 'none':
            args.append('-ansi')
            args.append('-std=' + std.value)
        return args

    def get_compile_only_args(self) -> T.List[str]:
        return []

    def get_no_optimization_args(self) -> T.List[str]:
        return ['-O0']

    def get_output_args(self, target: str) -> T.List[str]:
        return ['-o%s' % target]

    def get_werror_args(self) -> T.List[str]:
        return ['-change_message=error']

    def get_include_args(self, path: str, is_system: bool) -> T.List[str]:
        if path == '':
            path = '.'
        return ['-I' + path]

class CompCertCCompiler(CompCertCompiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo',
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        CompCertCompiler.__init__(self)

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99']
        return opts

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        return []

    def get_no_optimization_args(self) -> T.List[str]:
        return ['-O0']

    def get_output_args(self, target: str) -> T.List[str]:
        return ['-o{}'.format(target)]

    def get_werror_args(self) -> T.List[str]:
        return ['-Werror']

    def get_include_args(self, path: str, is_system: bool) -> T.List[str]:
        if path == '':
            path = '.'
        return ['-I' + path]

class C2000CCompiler(C2000Compiler, CCompiler):
    def __init__(self, exelist: T.List[str], version: str, for_machine: MachineChoice,
                 is_cross: bool, info: 'MachineInfo',
                 exe_wrapper: T.Optional['ExternalProgram'] = None,
                 linker: T.Optional['DynamicLinker'] = None,
                 full_version: T.Optional[str] = None):
        CCompiler.__init__(self, exelist, version, for_machine, is_cross,
                           info, exe_wrapper, linker=linker, full_version=full_version)
        C2000Compiler.__init__(self)

    # Override CCompiler.get_always_args
    def get_always_args(self) -> T.List[str]:
        return []

    def get_options(self) -> 'KeyedOptionDictType':
        opts = CCompiler.get_options(self)
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        opts[key].choices = ['none', 'c89', 'c99', 'c11']
        return opts

    def get_no_stdinc_args(self) -> T.List[str]:
        return []

    def get_option_compile_args(self, options: 'KeyedOptionDictType') -> T.List[str]:
        args = []
        key = OptionKey('std', machine=self.for_machine, lang=self.language)
        std = options[key]
        if std.value != 'none':
            args.append('--' + std.value)
        return args

    def get_compile_only_args(self) -> T.List[str]:
        return []

    def get_no_optimization_args(self) -> T.List[str]:
        return ['-Ooff']

    def get_output_args(self, target: str) -> T.List[str]:
        return ['--output_file=%s' % target]

    def get_werror_args(self) -> T.List[str]:
        return ['-change_message=error']

    def get_include_args(self, path: str, is_system: bool) -> T.List[str]:
        if path == '':
            path = '.'
        return ['--include_path=' + path]
