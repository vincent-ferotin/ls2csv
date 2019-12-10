#!/usr/bin/env python3.7
# -*- -coding: utf-8 -*-
"""Custom version of `ls`.
"""
from argparse import ArgumentParser
from collections import namedtuple
from enum import (
    Enum,
    unique,
)
from hashlib import md5
from os import (
    fsdecode,
    getcwd,
    readlink,
    scandir,
)
from os.path import (
    exists,
    getatime,
    getctime,
    getmtime,
    getsize,
    isdir,
    isfile,
    islink,
    join,
)
from pathlib import Path
from re import compile as compile_
from subprocess import (
    CalledProcessError,
    PIPE,
    run,
    STDOUT,
)
from time import sleep
from sys import (
    argv,
    exit,
    stdout,
)


# Constants  ----------------------------------------------------------------

SCRIPT_DESC = "Custom version of `ls` outputin results in CSV format."

# `ls` command and options:
#
# -A:   id. --all but skip `.` and `..`
# -l:   long, detailled output
# -1:   list one file per line
# -s    print the allocated size of each file, in blocks
# #-h:   size in human readable
# #-p:   append `/` indicator to directories  # NO: with -Q results in ``"dir"/``
# -Q:   enclose entry names in double quotes
# -Z:   print any security context of each file
#LS = "ls -A -l --full-time -s -1 -Q -Z \"{path}\""
LS = "ls -A -l -Q -Z --time-style=long-iso \"{path}\""
# Same but for directory
#LSD = "ls -A -l --full-time -s -1 -Q -Z -d \"{path}\""
LSD = "ls -A -l -Q -Z --time-style=long-iso -d \"{path}\""

LS_OUTPUT_REGEX = compile_(''
    r'^'
    # Type
    r'(?P<p_type>l|d|r|-)'
    # Permissions
    r'(?P<p_perms>((r|-)(w|-)(x|X|s|t|S|T|-)){3})'
    r'\s+'
    # Links number
    r'(?P<links_nb>\d+)'
    r'\s+'
    # User owner
    r'(?P<user_owner>[a-zA-Z][a-zA-Z0-9_]*)'
    r'\s+'
    # Group owner
    r'(?P<group_owner>[a-zA-Z][a-zA-Z0-9_]*)'
    r'\s+'
    # Security
    r'(?P<security>\S+)'
    r'\s+'
    # Size
    r'(?P<size>\d+)'
    r'\s+'
    # ISO day date
    r'(?P<iso_date>\d{4}-\d{2}-\d{2})'
    r'\s+'
    # ISO time
    r'(?P<iso_time>\d{2}:\d{2})'
    r'\s+'
    # path
    r'\"(?P<path>[^"]+)\"'
    # optional link
    r'('
        r'\s+'
        r'(-[>>]{1})'
        r'\s+'
        r'\"(?P<symlink>[^"]+)\"'
    r')?'
    r'$'
)

# AlwaysData locale, as defined:
#
#   $ locale
#   LANG=en_US.UTF-8
#   LANGUAGE=
#   LC_CTYPE="en_US.UTF-8"
#   LC_NUMERIC="en_US.UTF-8"
#   LC_TIME="en_US.UTF-8"
#   LC_COLLATE="en_US.UTF-8"
#   LC_MONETARY="en_US.UTF-8"
#   LC_MESSAGES="en_US.UTF-8"
#   LC_PAPER="en_US.UTF-8"
#   LC_NAME="en_US.UTF-8"
#   LC_ADDRESS="en_US.UTF-8"
#   LC_TELEPHONE="en_US.UTF-8"
#   LC_MEASUREMENT="en_US.UTF-8"
#   LC_IDENTIFICATION="en_US.UTF-8"
#   LC_ALL=
ENCODING="utf-8"

DEFAULT_EXCLUDED = [
    r"\.cache/",
    r"\.composer/",
    r"admin/mail/",
    r"admin/config/apache/run/",
]

DEFAULT_TIME_SLEEP = 0.33


# Classes  ------------------------------------------------------------------

@unique
class NodeType(Enum):
    error = 0
    directory = 1
    file = 2
    symlink = 3

    def as_ls_output_char(self):
        equivalence = {
            NodeType.file: "-",
            NodeType.directory: "d",
            NodeType.symlink: "l"
        }
        return equivalence[self]


@unique
class SymLinkType(Enum):
    error = 0
    directory = 1
    file = 2
    symlink = 3
    broken = 5


PossibleSymLink = namedtuple('PossibleSymLink', (
    'path',
    'link_type',
    'linked_path',
    'error_msg'))


@unique
class OwnerType(Enum):
    user = 1
    group = 2
    other = 3


class NodeInfos:
    """Metadata about a filesystem node.
    """
    def __init__(self, path, type_,
                 links_nb=None, size=None,
                 perms=None, user_owner=None, group_owner=None, security=None,
                 atime=None, mtime=None, ctime=None,
                 symlink=None, symlink_type=None, md5sum=None,
                 error_msgs=None):
        self._path = Path(path) if isinstance(path, str) else path
        self._type = type_
        self._links_nb = links_nb
        if isinstance(size, int):
            size = Size(size)
        self._size = size
        self._perms = perms
        self._user_owner = user_owner
        self._group_owner = group_owner
        self._security = security
        self._atime = atime
        self._mtime = mtime
        self._ctime = ctime
        self._symlink = symlink
        self._symlink_type = symlink_type
        self._md5sum = md5sum

        self._error_msgs = []
        if error_msgs:
            if isinstance(error_msgs, str):
                self._error_msgs.append(error_msgs)
            else:  # assume iterable
                self._error_msgs.extend([msg for msg in error_msgs])

    @property
    def path(self, relative_to=None):
        if relative_to:
            return str(self._path.relative_to(relative_to))
        # else:
        return str(self._path)

    @property
    def type(self):
        return self._type

    @property
    def links_nb(self):
        return self._links_nb

    @property
    def size(self):
        return self._size

    @property
    def perms(self):  # TODO: param: OwnerType
        return self._perms

    @property
    def user_owner(self):
        return self._user_owner

    @property
    def group_owner(self):
        return self._group_owner

    @property
    def security(self):
        return self._security

    @property
    def mtime(self):  # TODO: param: format, TZ
        return self._mtime

    @property
    def atime(self):  # TODO: param: format, TZ
        return self._atime

    @property
    def ctime(self):  # TODO: param: format, TZ
        return self._ctime

    @property
    def symlink(self):
        return self._symlink

    @property
    def symlink_type(self):
        return self._symlink_type

    @property
    def md5sum(self):
        return self._md5sum

    @md5sum.setter
    def md5sum(self, md5sum):
        self._md5sum = md5sum

    @property
    def error_msgs(self):
        return " | ".join(self._error_msgs)

    def add_error_msg(self, new_error_msg):
        self._error_msgs.append(new_error_msg)

    @staticmethod
    def colstocsv():
        return tocsv([
            "Path",
            "Type",
            "Links nb.",
            "Size (b)",
            "Size (-h)",
            "Permissions",
            "User owner",
            "Group owner",
            "Security infos.",
            "atime (last access, in s. since Epoch)",
            "mtime (last mod., in s. since Epoch)",
            "ctime (last metada change, in s. since Epoch)",
            "Sym.Link to path",
            "Type of Sym.Link",
            "Error message(s)",
        ])

    def tocsv(self):
        return tocsv([
            self.path,
            self.type,
            self.links_nb,
            self.size.value,
            self.size.convert_to(),
            self.perms,
            self.user_owner,
            self.group_owner,
            self.security,
            self.atime,
            self.mtime,
            self.ctime,
            self.symlink,
            self.symlink_type,
            self.error_msgs,
        ])


FileContent = namedtuple('FileContent', (
    'content',
    'error_msg'))


class Size:
    """Size of a filesystem node.
    """
    UNITS = ['b', 'Kb', 'Mb', 'Gb', 'Tb']

    def __init__(self, value, unit=None):
        """Initialize a new size.

        Arguments
        ---------
        value : `int`
            Size of a node. If no :param:`unit` is set, assume it is in bytes.
        size : `str`
            Unit of bytes in which :param:`size` is expressed, one of `.UNITS`.
            If not set, will default to ``'b'``.
        """
        self._value = value
        unit = self.UNITS[0] if (unit is None) else unit
        if unit not in self.UNITS:
            error_msg = (""
                f"Unit of size must belong to `Size.UNITS`, and be one of "
                f"``{self.UNITS}``; parameter was set to ``'{unit}'``!")
            raise ValueError(error_msg)
        self._unit = unit

    @property
    def value(self):
        return self._value

    @property
    def unit(self):
        return self._unit

    def convert_to(self, unit=None):
        """Convert a given size in bytes into either human or in a given unit.

        Arguments
        ---------
        unit : `str`
            One of `.UNITS`.
            If set to ``None`` (default), try to operate a conversion like
            a human  would do.

        Returns
        -------
        `str`
            Size object representation into :param:`unit` unit.
        """
        if unit not in (self.UNITS[:] + [None]):
            error_msg = (""
                f"Unit of size must belong to `Size.UNITS`, and be one of "
                f"``{self.UNITS}``; parameter was set to ``'{unit}'``!")
            raise ValueError(error_msg)

        divisor = 1024

        if (unit == self.unit) or \
                ((unit is None) and (self.unit == 'b') and (self.value < divisor)):
            return f"{self.value} {self.unit}"

        dividend = self.value
        for exp, _unit in enumerate(self.UNITS[1:], start=1):
            _size = float(dividend) / float(divisor)
            if (_unit == self.unit) or ((unit is None) and (_size < divisor)):
                return f"{_size:.1f} {_unit}"
            dividend = _size

        return f"{_size:.1f} {_unit}"


# Functions  ----------------------------------------------------------------

def tocsv(row):
    """Convert a list of values into a CSV row.

    Arguments
    ---------
    row : `iterable`
        List of cell values to transform into CSV.

    Returns
    `str`
        CSV row.
    """
    _row = []
    for item in row:
        if item is None:
            _row.append("")
        elif isinstance(item, (int, float, bool)):
            _row.append(str(item))
        elif isinstance(item, str):
            item = item.strip()\
                       .strip('"')\
                       .replace('"', r'\"')
            _row.append(f'"{item}"' if (len(item) > 0) else "")
        elif isinstance(item, Enum):
            _row.append(f'"{item.name}"')
        else:  # generic
            item = str(item)\
                       .strip('"')\
                       .replace('"', r'\"')
            _row.append(f'"{item}"' if (len(item) > 0) else "")

    return ",".join(_row)


def write_new_line(filepath=None, encoding=None, content=None):
    """Write new line with content at end of given file.

    Arguments
    ---------
    filepath : `str`
        Filepath into which append content; if set to ``None``, output
        will be `sys.stdout`.
    encoding : `str`
        Encoding of :param:`file` if not `sys.stdout`.
    content : `str`
        Content to append as new line in :param:`file`.
    """
    if content is None:
        return
    #else:

    if filepath is None:
        print(content)
        return
    #else:

    with open(filepath, mode='at', encoding=encoding) as file_:
            file_.write(content + "\n")


def match_none(path, regex_patterns):
    """Tell if a given path match none of all regex patterns.

    Arguments
    ---------
    path : `str`
        Directory or file path to test again list of regexp patterns.
    regex_patterns : `iterable` of `re.regex`
        List of patterns to test :param:`path` against.

    Returns
    -------
    `bool`
        ``True`` if all patterns of :param:`regex_patterns` do *not* match
        :param:`path` (i.e. none pattern match path);
        ``False`` otherwise (if any pattern match path).
    """
    for pattern in regex_patterns:
        match = pattern.match(path)
        if match:
            return False
    return True


def read_file_content(path):
    """Read a file content.

    Arguments
    ---------
    path : `str`
        File path from which try reading content.

    Returns
    -------
    `FileContent`
        Always, reading content wins or errs.
    """
    content, error_msg = (None,) * 2

    try:
        with open(path, mode='rb') as file_:
            content = file_.readall()
    except OSError as error:
        error_msg = (
            f"Unable to open and read ``{node_infos.path}`` file: "
            f"{error.strerror}")

    return FileContent(content, error_msg)


def get_symlink_infos(path, dir_entry=None):
    """Get some infos about a symbolic link.

    Arguments
    ---------
    path : `str`
        Path from which retrieve informations.
    dir_entry : `os.DirEntry`
        Item resulting of `os.scandir` on parent path, if any.

    Returns
    -------
    `PossibleSymLink`
        :param:`path` links informations.
    """
    try:
        linked_path = Path(readlink(path))
    except PermissionError as error:
        error_msg = (
            f"Sym.Link ``{path}`` is unreadable, asking for its actual linked "
            f"path results in a permission error: {error.strerror}")
        return PossibleSymLink(path, SymLinkType.error, None, error_msg)
    except OSError as error:
        error_msg = (
            f"Sym.Link ``{path}`` is unreadable, asking for its actual linked "
            f"path results in a OSError: {error.strerror}")
        return PossibleSymLink(path, SymLinkType.error, None, error_msg)

    if not linked_path.is_absolute():
        linked_path = Path(Path(path).parent / linked_path)
    try:
        linked_path = linked_path.resolve(strict=True)
    except FileNotFoundError as error:
        return PossibleSymLink(path, SymLinkType.broken, str(linked_path), None)

    try:
        if linked_path.is_dir():
            return PossibleSymLink(path, SymLinkType.directory, str(linked_path),
                                   None)
        elif linked_path.is_file():
            return PossibleSymLink(path, SymLinkType.file, str(linked_path),
                                   None)
        else:
            error_msg = (
                f"Linked path ``{linked_path}`` is neither a directory, a file "
                f"nor a symlink!")
            return PossibleSymLink(path, SymLinkType.error, str(linked_path),
                                   error_msg)
    except PermissionError as error:
        error_msg = (
            f"Access to linked path ``{linked_path}`` is unreachable, trying "
            f"to dertermine node's type results in a permission error: "
            f"{error.strerror}")
        return PossibleSymLink(path, SymLinkType.error, str(linked_path),
                               error_msg)
    except OSError as error:
        error_msg = (
            f"Access to linked path ``{linked_path}`` is unreachable, trying "
            f"to dertermine node's type results in an OSerror: "
            f"{error.strerror}")
        return PossibleSymLink(path, SymLinkType.error, str(linked_path),
                               error_msg)


def get_node_content_hash(node_infos):
    """Get a MD5 hash hexadecimal digest from content of a node.

    Arguments
    ---------
    node_infos : `NodeInfos`
        Node info whose path will be tried to be red for computing hash.

    Returns
    -------
    `str` or ``None``
        MD5 sum hash as hexadecimal digest if node is an actual file and
        reading its content was possible; ``None`` otherwise.
    """
    type_ = node_infos.type
    if (type_ == NodeType.error) or (type_ != NodeType.file):
        return None
    # else:

    file_content = read_file_content(node_infos.path)
    if file_content.error_msg:
        node_infos.add_error_msg(error_msg)
        return None
    # else:

    hash_ = md5()
    hash_.update(content)

    return hash_.hexdigest()


def get_node_infos(path, dir_entry):
    """Get some file tree node informations.

    Arguments
    ---------
    path : `str`
        Absolute path from which retrieve informations.
    dir_entry : `os.DirEntry`
        Item resulting of `os.scandir` on parent path.

    Returns
    -------
    NodeInfos
        Informations about node located at :param:`path`.
    """
    symlinked_path, symlink_type = (None,) * 2
    error_msgs = []

    # type
    try:
        if dir_entry.is_file(follow_symlinks=False):
            type_ = NodeType.file
        elif dir_entry.is_dir(follow_symlinks=False):
            type_ = NodeType.directory
        elif dir_entry.is_symlink():
            type_ = NodeType.symlink
            possible_symlink = get_symlink_infos(path)
            symlinked_path = possible_symlink.linked_path
            symlink_type = possible_symlink.link_type
            if possible_symlink.error_msg:
                error_msgs.append(possible_symlink.error_msg)
        else:
            error_msg = \
                f"Path ``{path}`` is neither a directory, a file or a symlink!"
            return NodeInfos(path, NodeType.error, error_msgs=error_msg)
    except PermissionError as error:
        error_msg = (
            f"Path ``{path}`` is unreachable, asking for its type results in "
            f"a permission error: {error.strerror}")
        return NodeInfos(path, NodeType.error, error_msgs=error_msg)
    except OSError as error:
        error_msg = (
            f"Checking for ``{path}`` type results in an OSError: "
            f"{error.strerror}")
        return NodeInfos(path, NodeType.error, error_msgs=error_msg)

    # ls
    try:
        ls_cmd = LSD.format(path=path) if (type_ == NodeType.directory) \
                                      else LS.format(path=path)
        completed_process = run(ls_cmd, shell=True, check=True, stdout=PIPE,
                                stderr=STDOUT, encoding=ENCODING)
        output = completed_process.stdout.rstrip()
    except CalledProcessError as error:
        error_msgs.append(f"Failed to run `ls` on ``{path}``: {error.stderr}")
        return NodeInfos(path, NodeType.error, error_msgs=error_msgs)

    match = LS_OUTPUT_REGEX.match(output)
    if match is None:
        error_msgs.append(f"Unable to parse output of `ls` command: ``{output}``")
        return NodeInfos(path, NodeType.error, error_msgs=error_msgs)
    # else:

    # retrieve property values from parsed regexp
    p_type, perms, links_nb, \
        user_owner, group_owner, \
        security, size = match.group('p_type', 'p_perms', 'links_nb',
                                     'user_owner', 'group_owner', 'security',
                                     'size')
    #   Conversions from string to specific types
    links_nb = int(links_nb)
    size = int(size)

    atime, mtime, ctime = (None,) * 3

    # Check if types are coherents
    if p_type != type_.as_ls_output_char():
        error_msg = (
            f"Types differ from Python :mod:`os.scandir` result and output of "
            f"`ls` command: ``{type_}`` (Python) != ``{p_type}`` (ls)!")
        error_msgs.append(error_msg)
        return NodeInfos(path, NodeType.error, error_msgs=error_msgs)

    # Get (a|m|c)time(s)
    try:
        atime = getatime(path)
        mtime = getmtime(path)
        ctime = getctime(path)
    except OSError as error:
        error_msgs.append("Unable to retrieve either atime, mtime and/or ctime!")

    return NodeInfos(path, type_,
                     links_nb=links_nb, size=size,
                     perms=perms, user_owner=user_owner, group_owner=group_owner,
                     security=security,
                     atime=atime, mtime=mtime, ctime=ctime,
                     symlink=symlinked_path, symlink_type=symlink_type,
                     error_msgs=error_msgs)


def _walk(path, excluded=None, sleep_time=DEFAULT_TIME_SLEEP):
    """Walk a given path.

    Arguments
    ---------
    path : `str`
        Absolute path to walk.
    excluded : `list` of regexp
        List of regexp. to exclude from further walking.
    sleep_time : `float`
        Approx. time to sleep, in seconds, between running two successive
        `ls` commands on *files* (not directories).

    Yields
    ------
    `iterable` of `NodeInfos`
        List of output of shell command `ls` on walked paths as constructed
        `Node` instances.
    """
    if excluded is None:
        excluded = []

    with scandir(path) as dir_entries:
        for dir_entry in dir_entries:
            path = fsdecode(dir_entry.path)
            if match_none(path, excluded):
                node = get_node_infos(path, dir_entry)

                yield node
                sleep(sleep_time)

                if node.type == NodeType.directory:
                    yield from _walk(path, excluded=excluded,
                                     sleep_time=sleep_time)


# CLI  ----------------------------------------------------------------------

def create_args_parser():
    """Create a CLI arguments parser.
    """
    parser = ArgumentParser(description=SCRIPT_DESC)
    parser.add_argument('--exclude', default=",".join(DEFAULT_EXCLUDED),
                        help=("list of paths to exclude from walking, comma "
                              "separated (e.g. 'path1,path2')."))
    parser.add_argument('--sleep', type=float, default=DEFAULT_TIME_SLEEP,
                        help=("Approx. time to sleep, in seconds, between "
                              "running two successive `ls` commands on *files* "
                              "(not directories). Could be expressed as float."))
    parser.add_argument('-o', '--output',
                        help=("Output CSV filepath where store results of "
                              "`ls` command traversing files tree. "
                              "If not set, `stdout` will be used instead."))
    parser.add_argument('path', nargs='?',
                        help="path to walk. Default to `.`.")
    return parser


# Main  ---------------------------------------------------------------------

def extend_excluded(excluded, script_path, path_to_walk, output_path=None):
    """Enhance path excluded list with current script name and optional
    output path.

    Arguments
    ---------
    excluded: `list` of `str`
        Existing list of patterns to exclude.
    script_path : :class:`pathlib.Path`
        This current script absolute path, as called by Python.
    path_to_walk : :class:`pathlib.Path`
        Absolute path to walk.
    output_path : :class:`pathlib.Path`
        CSV output absolute filepath, if any.

    Returns
    -------
    `list` of regexp. objects
        New list of excluded patterns, enhanced with :param:`script_path` and
        :param:`output_path`.
    """
    _excluded = []

    # Transform any path in absolute :class:`pathlib.Path`
    for path in excluded:
        path = Path(path)
        if not path.is_absolute():
            # path is assumed relative to `path_to_walk`
            _excluded.append(path_to_walk / path)
        else:
            _excluded.append(path)

    # Append current paths to excluded list
    _excluded.append(script_path)
    if output_path:
        _excluded.append(output_path)

    # Transform path in regex objects
    excluded = []
    for path in _excluded:
        path = str(path)
        if path.endswith('/'):
            path += '.*'
        excluded.append(compile_(f"^{path}$"))

    return excluded


def main():
    """Main function, software entrypoint.
    """
    this_script_path = Path(argv[0]).resolve()
    working_dir = Path(getcwd()).resolve()

    args_parser = create_args_parser()
    args = args_parser.parse_args()

    # Parse and adjust options
    path = "." if (args.path is None) else args.path
    try:
        path_to_walk = Path(path).resolve(strict=True)
    except FileNotFoundError as error:
        error_msg = f"Unable to reach ``{path}`` path to walk on it!"
        exit(error_msg)

    #   Manage output
    output_path = None if (('output' not in args) or (args.output is None)) \
                       else Path(args.output).resolve()
    if output_path and Path(output_path).resolve().exists():
        error_msg = (
            f"Output filepath ``{output_path}`` already exists: could not "
            "write in it!")
        exit(error_msg)

    #   Construct list of pathes to exclude
    excluded = [] if ("exclude" not in args) \
                    else set(args.exclude.split(','))
    excluded = extend_excluded(excluded, script_path=this_script_path,
                               path_to_walk=path_to_walk,
                               output_path=output_path)

    # Walk tree and print dir. entries metadata:
    write_new_line(output_path, ENCODING, NodeInfos.colstocsv())
    for node_infos in _walk(str(path_to_walk), excluded, args.sleep):
        write_new_line(output_path, ENCODING, node_infos.tocsv())

    return 0


if __name__ == '__main__':
    exit(main())

