import ast
import codecs
import os.path
import posixpath
import subprocess
import sys


def script(
    path,
    add_python_modules=None,
    add_python_paths=None,
    python_binary=None,
    copy_shebang=False,
):
    if add_python_modules is None:
        add_python_modules = []

    if add_python_paths is None:
        add_python_paths = []

    python_paths = [os.path.dirname(path)] + add_python_paths + _read_sys_path_from_python_bin(python_binary)

    output = []

    output.append(_generate_shebang(path, copy=copy_shebang))
    output.append(_prelude())
    output.append(_generate_module_writers(
        path,
        sys_path=python_paths,
        add_python_modules=add_python_modules,
    ))
    output.append(_indent(open(path).read()))
    output.append("\n\n")
    output.append(_epilogue())
    return "".join(output)

def _read_sys_path_from_python_bin(binary_path):
    if binary_path is None:
        return []
    else:
        output = subprocess.check_output(
            [binary_path, "-E", "-c", "import sys;\nfor path in sys.path: print(path)"],
        )
        return [
            # TODO: handle non-UTF-8 encodings
            line.strip().decode("utf-8")
            for line in output.split(b"\n")
            if line.strip()
        ]

def _indent(string):
    return "    " + string.replace("\n", "\n    ")

def _generate_shebang(path, copy):
    if copy:
        with open(path) as script_file:
            first_line = script_file.readline()
            if first_line.startswith("#!"):
                return first_line

    return "#!/usr/bin/env python"

def _prelude():
    prelude_path = os.path.join(os.path.dirname(__file__), "prelude.py")
    with open(prelude_path) as prelude_file:
        return prelude_file.read()

def _epilogue():
    epilogue_path = os.path.join(os.path.dirname(__file__), "epilogue.py")
    with open(epilogue_path) as epilogue_file:
        return epilogue_file.read()

def _generate_module_writers(path, sys_path, add_python_modules):
    generator = ModuleWriterGenerator(sys_path)
    generator.generate_for_file(path, add_python_modules=add_python_modules)
    return generator.build()

class ModuleWriterGenerator(object):
    def __init__(self, sys_path):
        self._sys_path = sys_path
        self._modules = {}

    def build(self):
        output = []
        for module_path, module_source in _iteritems(self._modules):
            minified_source = self._minify(module_source, module_path)
            output.append("    __stickytape_write_module({0}, {1})\n".format(
                _string_escape(module_path),
                _string_escape(minified_source)
            ))
        return "".join(output)

    def generate_for_file(self, python_file_path, add_python_modules):
        self._generate_for_module(ImportTarget(python_file_path, "."))

        for add_python_module in add_python_modules:
            import_line = ImportLine(import_path=add_python_module, items=[])
            self._generate_for_import(python_module=None, import_line=import_line)

    def _generate_for_module(self, python_module):
        import_lines = _find_imports_in_file(python_module.absolute_path)
        for import_line in import_lines:
            if not _is_stdlib_import(import_line):
                self._generate_for_import(python_module, import_line)

    def _generate_for_import(self, python_module, import_line):
        import_targets = self._read_possible_import_targets(python_module, import_line)

        for import_target in import_targets:
            if import_target.module_path not in self._modules:
                self._modules[import_target.module_path] = import_target.read()
                self._generate_for_module(import_target)

    def _minify(self, source, module_path):
        from pyminifier.compression import gz_pack
        # from pyminifier.minification import minify
        # from pyminifier.token_utils import listified_tokenizer
        # from types import SimpleNamespace

        # if "natsort" not in module_path:
        #     tokens = listified_tokenizer(source)
        #     options = SimpleNamespace()
        #     options.tabs = False
        #     source = minify(tokens, options)

        return gz_pack(source)

    def _read_possible_import_targets(self, python_module, import_line):
        import_path_parts = import_line.import_path.split("/")
        possible_init_module_paths = [
            posixpath.join(posixpath.join(*import_path_parts[0:index + 1]), "__init__.py")
            for index in range(len(import_path_parts))
        ]

        possible_module_paths = [import_line.import_path + ".py"] + possible_init_module_paths

        for item in import_line.items:
            possible_module_paths += [
                posixpath.join(import_line.import_path, item + ".py"),
                posixpath.join(import_line.import_path, item, "__init__.py")
            ]

        import_targets = [
            self._find_module(python_module, module_path)
            for module_path in possible_module_paths
        ]

        valid_import_targets = [target for target in import_targets if target is not None]
        return valid_import_targets
        # TODO: allow the user some choice in what happens in this case?
        # Detection of try/except blocks is possibly over-complicating things
        #~ if len(valid_import_targets) > 0:
            #~ return valid_import_targets
        #~ else:
            #~ raise RuntimeError("Could not find module: " + import_line.import_path)

    def _find_module(self, importing_python_module, module_path):
        if importing_python_module is not None:
            relative_module_path = os.path.join(os.path.dirname(importing_python_module.absolute_path), module_path)
            if os.path.exists(relative_module_path):
                return ImportTarget(relative_module_path, os.path.join(os.path.dirname(importing_python_module.module_path), module_path))

        for sys_path in self._sys_path:
            full_module_path = os.path.join(sys_path, module_path)
            if os.path.exists(full_module_path):
                return ImportTarget(full_module_path, module_path)
        return None


def _find_imports_in_file(file_path):
    source = _read_file(file_path)
    parse_tree = ast.parse(source, file_path)

    for node in ast.walk(parse_tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                yield ImportLine(name.name, [])

        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                module = "."
            else:
                module = node.module
            yield ImportLine(module, [name.name for name in node.names])


def _resolve_package_to_import_path(package):
    import_path = package.replace(".", "/")
    if import_path.startswith("/"):
        return "." + import_path
    else:
        return import_path

def _read_file(path):
    with open(path) as file:
        return file.read()

def _is_stdlib_import(import_line):
    return import_line.import_path in _stdlib_modules

class ImportTarget(object):
    def __init__(self, absolute_path, module_path):
        self.absolute_path = absolute_path
        self.module_path = posixpath.normpath(module_path)

    def read(self):
        return _read_file(self.absolute_path)

class ImportLine(object):
    def __init__(self, import_path, items):
        self.import_path = _resolve_package_to_import_path(import_path)
        self.items = items

_stdlib_modules = set([
    "string",
    "re",
    "struct",
    "difflib",
    "StringIO",
    "cStringIO",
    "textwrap",
    "codecs",
    "unicodedata",
    "stringprep",
    "fpformat",

    "datetime",
    "calendar",
    "collections",
    "heapq",
    "bisect",
    "array",
    "sets",
    "sched",
    "mutex",
    "Queue",
    "weakref",
    "UserDict",
    "UserList",
    "UserString",
    "types",
    "new",
    "copy",
    "pprint",
    "repr",

    "numbers",
    "math",
    "cmath",
    "decimal",
    "fractions",
    "random",
    "itertools",
    "functools",
    "operator",

    "os/path",
    "fileinput",
    "stat",
    "statvfs",
    "filecmp",
    "tempfile",
    "glob",
    "fnmatch",
    "linecache",
    "shutil",
    "dircache",
    "macpath",

    "pickle",
    "cPickle",
    "copy_reg",
    "shelve",
    "marshal",
    "anydbm",
    "whichdb",
    "dbm",
    "gdbm",
    "dbhash",
    "bsddb",
    "dumbdbm",
    "sqlite3",

    "zlib",
    "gzip",
    "bz2",
    "zipfile",
    "tarfile",

    "csv",
    "ConfigParser",
    "robotparser",
    "netrc",
    "xdrlib",
    "plistlib",

    "hashlib",
    "hmac",
    "md5",
    "sha",

    "os",
    "io",
    "time",
    "argparse",
    "optparse",
    "getopt",
    "logging",
    "logging/config",
    "logging/handlers",
    "getpass",
    "curses",
    "curses/textpad",
    "curses/ascii",
    "curses/panel",
    "platform",
    "errno",
    "ctypes",

    "select",
    "threading",
    "thread",
    "dummy_threading",
    "dummy_thread",
    "multiprocessing",
    "mmap",
    "readline",
    "rlcompleter",

    "subprocess",
    "socket",
    "ssl",
    "signal",
    "popen2",
    "asyncore",
    "asynchat",

    "email",
    "json",
    "mailcap",
    "mailbox",
    "mhlib",
    "mimetools",
    "mimetypes",
    "MimeWriter",
    "mimify",
    "multifile",
    "rfc822",
    "base64",
    "binhex",
    "binascii",
    "quopri",
    "uu",

    "HTMLParser",
    "sgmllib",
    "htmllib",
    "htmlentitydefs",
    "xml/etree/ElementTree",
    "xml/dom",
    "xml/dom/minidom",
    "xml/dom/pulldom",
    "xml/sax",
    "xml/sax/handler",
    "xml/sax/saxutils",
    "xml/sax/xmlreader",
    "xml/parsers/expat",

    "webbrowser",
    "cgi",
    "cgitb",
    "wsgiref",
    "urllib",
    "urllib2",
    "httplib",
    "ftplib",
    "poplib",
    "imaplib",
    "nntplib",
    "smtplib",
    "smtpd",
    "telnetlib",
    "uuid",
    "urlparse",
    "SocketServer",
    "BaseHTTPServer",
    "SimpleHTTPServer",
    "CGIHTTPServer",
    "cookielib",
    "Cookie",
    "xmlrpclib",
    "SimpleXMLRPCServer",
    "DocXMLRPCServer",

    "audioop",
    "imageop",
    "aifc",
    "sunau",
    "wave",
    "chunk",
    "colorsys",
    "imghdr",
    "sndhdr",
    "ossaudiodev",

    "gettext",
    "locale",

    "cmd",
    "shlex",

    "Tkinter",
    "ttk",
    "Tix",
    "ScrolledText",
    "turtle",

    "pydoc",
    "doctest",
    "unittest",
    "test",
    "test/test_support",

    "bdb",
    "pdb",
    "hotshot",
    "timeit",
    "trace",

    "sys",
    "sysconfig",
    "__builtin__",
    "future_builtins",
    "__main__",
    "warnings",
    "contextlib",
    "abc",
    "atexit",
    "traceback",
    "__future__",
    "gc",
    "inspect",
    "site",
    "user",
    "fpectl",
    "distutils",

    "code",
    "codeop",

    "rexec",
    "Bastion",

    "imp",
    "importlib",
    "imputil",
    "zipimport",
    "pkgutil",
    "modulefinder",
    "runpy",

    "parser",
    "ast",
    "symtable",
    "symbol",
    "token",
    "keyword",
    "tokenize",
    "tabnanny",
    "pyclbr",
    "py_compile",
    "compileall",
    "dis",
    "pickletools",

    "formatter",

    "msilib",
    "msvcrt",
    "_winreg",
    "winsound",

    "posix",
    "pwd",
    "spwd",
    "grp",
    "crypt",
    "dl",
    "termios",
    "tty",
    "pty",
    "fcntl",
    "pipes",
    "posixfile",
    "resource",
    "nis",
    "syslog",
    "commands",

    "ic",
    "MacOS",
    "macostools",
    "findertools",
    "EasyDialogs",
    "FrameWork",
    "autoGIL",
    "ColorPicker",

    "gensuitemodule",
    "aetools",
    "aepack",
    "aetypes",
    "MiniAEFrame",

    "al",
    "AL",
    "cd",
    "fl",
    "FL",
    "flp",
    "fm",
    "gl",
    "DEVICE",
    "GL",
    "imgfile",
    "jpeg",

    "sunaudiodev",
    "SUNAUDIODEV",
])


if sys.version_info[0] == 2:
    _iteritems = lambda x: x.iteritems()

    def _string_escape(string):
        return "'''{0}'''".format(codecs.getencoder("string_escape")(string)[0].decode("ascii"))

else:
    _iteritems = lambda x: x.items()

    _string_escape = repr

    _stdlib_modules.add("asyncio")
