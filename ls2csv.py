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
from datetime import datetime
from hashlib import md5
from logging import (
    FileHandler,
    Formatter,
    getLogger,
    INFO,
    StreamHandler,
)
from os import (
    fsdecode,
    getcwd,
    getpid,
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
from random import uniform
from re import (
    compile as compile_,
    escape,
)
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
    stderr,
)


# Constants  ----------------------------------------------------------------

SCRIPT_DESC = "Custom version of `ls` output in results in CSV format."

LOGGER = getLogger(__name__)

DEFAULT_LOG_LEVEL = INFO

DEFAULT_CHECKSUM_ALGORITHM = "md5"
HASH_FUNCTIONS = {
    'md5': md5,
}

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
    r".cache/",
    r".composer/",
    r"admin/mail/",
    r"admin/config/apache/run/",
]

DEFAULT_MIN_TIME_SLEEP = 0.25
DEFAULT_MAX_TIME_SLEEP = 0.35


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
                 symlink=None, symlink_type=None, checksums=None,
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
        self._atime = None
        if atime:
            if isinstance(atime, float):
                self._atime = datetime.fromtimestamp(atime)
            elif isinstance(atime, datetime):
                self._atime = atime
        self._mtime = None
        if mtime:
            if isinstance(mtime, float):
                self._mtime = datetime.fromtimestamp(mtime)
            elif isinstance(mtime, datetime):
                self._mtime = mtime
        self._ctime = None
        if ctime:
            if isinstance(ctime, float):
                self._ctime = datetime.fromtimestamp(ctime)
            elif isinstance(ctime, datetime):
                self._ctime = ctime
        self._symlink = Path(symlink) if isinstance(symlink, str) else symlink
        self._symlink_type = symlink_type
        self._checksums = dict()
        if checksums:
            self._checksums.update(checksums)

        self._error_msgs = []
        if error_msgs:
            if isinstance(error_msgs, str):
                self._error_msgs.append(error_msgs)
            else:  # assume iterable
                self._error_msgs.extend([msg for msg in error_msgs])

    @property
    def path(self):
        if self._path is None:
            return None
        # else:
        return Path(self._path)

    def get_path(self, relative_to=None):
        if self._path is None:
            return None
        # else:
        if relative_to is None:
            return self.path
        # else:
        try:
            _path = self._path.relative_to(relative_to)
        except ValueError as error:
            _path = self._path
        return str(_path)

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
    def mtime(self):
        return self._mtime

    @property
    def mtime_as_timestamp(self):
        return None if (self._mtime is None) else self._mtime.timestamp()

    @property
    def mtime_as_isoformat(self):
        return None if (self._mtime is None) else self._mtime.isoformat()

    @property
    def atime(self):
        return self._atime

    @property
    def atime_as_timestamp(self):
        return None if (self._atime is None) else self._atime.timestamp()

    @property
    def atime_as_isoformat(self):
        return None if (self._atime is None) else self._atime.isoformat()

    @property
    def ctime(self):
        return self._ctime

    @property
    def ctime_as_timestamp(self):
        return None if (self._ctime is None) else self._ctime.timestamp()

    @property
    def ctime_as_isoformat(self):
        return None if (self._ctime is None) else self._ctime.isoformat()

    @property
    def symlink(self):
        if self._symlink is None:
            return None
        # else:
        return str(self._symlink)

    def get_symlink(self, relative_to=None):
        if self._symlink is None:
            return None
        # else:
        if relative_to is None:
            return self.symlink
        # else:
        try:
            _symlink = self._symlink.relative_to(relative_to)
        except ValueError as error:
            _symlink = self._symlink
        return str(_symlink)

    @property
    def symlink_type(self):
        return self._symlink_type

    @property
    def checksums(self):
        return self._checksums.copy()

    def add_checksum(self, algorithm, checksum):
        """Add a checksum to checksums collection.

        Arguments
        ---------
        algorithm : `str`
            Algorithm used for generating :param:`checksum`.
        checksum : `str`
            Hexadecimal digest of checksum of current node's content with
            :param:`algorithm`.
        """
        self._checksums[algorithm] = checksum

    def get_checksum(self, algorithm=DEFAULT_CHECKSUM_ALGORITHM):
        """Get current checksum for a given algorithm.

        Arguments
        ---------
        algorithm : `str`
            Algorithm used for generating desired checksum.

        Returns
        -------
        `str` or ``None``
            Hexadecimal digest of checksum of current node's content with
            :param:`algorithm`.
        """
        return self._checksums.get(algorithm)

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
            "atime (ISO 8601 format)",
            "mtime (last mod., in s. since Epoch)",
            "atime (ISO 8601 format)",
            "ctime (last metada change, in s. since Epoch)",
            "ctime (ISO 8601 format)",
            "Sym.Link to path",
            "Type of Sym.Link",
            "MD5 checksum",
            "Error message(s)",
        ])

    def tocsv(self, pathes_relative_to=None):
        return tocsv([
            self.get_path(relative_to=pathes_relative_to),
            self.type,
            self.links_nb,
            self.size.value,
            self.size.convert_to(),
            self.perms,
            self.user_owner,
            self.group_owner,
            self.security,
            self.atime_as_timestamp,
            self.atime_as_isoformat,
            self.mtime_as_timestamp,
            self.mtime_as_isoformat,
            self.ctime_as_timestamp,
            self.ctime_as_isoformat,
            self.get_symlink(relative_to=pathes_relative_to),
            self.symlink_type,
            self.get_checksum('md5'),
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


class AppRunInfos:
    """Set of informations about current application run.
    """
    def __init__(self):
        self._script_path = Path(argv[0]).resolve(strict=True)
        self._working_dirpath = Path(getcwd()).resolve(strict=True)
        self._pid = getpid()
        self._start_datetime = datetime.now()

    @property
    def script_path(self):
        return self._script_path

    @property
    def working_dirpath(self):
        return self._working_dirpath

    @property
    def pid(self):
        return self._pid

    @property
    def start_datetime(self):
        return self._start_datetime

    @property
    def start_datetime_as_isoformat(self):
        return self._start_datetime.isoformat()


class Options:
    """Script options.
    """
    def __init__(self, parsed_cli_args, walked_pathes,
                 min_sleep_time=None, max_sleep_time=None,
                 pathes_relative_to=None, output_path=None, logfile_path=None,
                 excluded=None, excluded_relative_to=None, checksum=None):
        self._parsed_cli_args = parsed_cli_args
        self._walked_pathes = list(walked_pathes)
        self._min_sleep_time = min_sleep_time
        self._max_sleep_time = max_sleep_time
        self._pathes_relative_to = pathes_relative_to
        self._output_path = output_path
        self._logfile_path = logfile_path
        self._excluded = []
        if excluded:
            self._excluded.extend([exclude for exclude in excluded])
        self._excluded_relative_to = excluded_relative_to
        self._checksum = checksum

    @property
    def parsed_cli_args(self):
        return self._parsed_cli_args

    @property
    def walked_pathes(self):
        return self._walked_pathes[:]

    @property
    def min_sleep_time(self):
        return self._min_sleep_time

    @property
    def max_sleep_time(self):
        return self._max_sleep_time

    @property
    def pathes_relative_to(self):
        return self._pathes_relative_to

    @property
    def output_path(self):
        return self._output_path

    @property
    def logfile_path(self):
        return self._logfile_path

    @property
    def excluded_regex(self):
        return self._excluded[:]

    @property
    def excluded_patterns(self):
        return [regex.pattern for regex in self._excluded]

    @property
    def excluded_relative_to(self):
        return self._excluded_relative_to

    @property
    def checksum(self):
        return self._checksum

    def get_path(self, path):
        """Get a filesystem path in a form respecting `pathes_relative_to`
        setting.

        Arguments
        ---------
        path : `pathlib.Path`
            Path from which get value respecting application options.

        Returns
        -------
        `pathlib.Path`
            New path with same value that :param:`path`, following
            `pathes_relative_to` setting.
        """
        if self._pathes_relative_to is None:
            return Path(path)
        # else:
        return path.relative_to(self.pathes_relative_to)

    def get_random_sleep_time(self):
        """Get random sleep time, regarding interval defined by `min_sleep_time`
        and `max_sleep_time`.

        Returns
        -------
        `float`
            Time to sleep (in s.).
        """
        return uniform(self.min_sleep_time, self.max_sleep_time)


# Functions  ----------------------------------------------------------------

def exit_on_error(msg, return_code=1):
    """Exit system on error.

    Arguments
    ---------
    msg : `str`
        Error message to print to `sys.stderr`.
    return_code : `int`
        Return code.
    """
    print(msg, file=stderr)
    exit(return_code)


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


def match_any(path, regex_patterns):
    """Tell if a given path match any of all regex patterns.

    Arguments
    ---------
    path : `pathlib.Path`
        Directory or file path to test again list of regexp patterns.
    regex_patterns : `iterable` of `re.regex`
        List of patterns to test :param:`path` against.

    Returns
    -------
    `bool`
        ``True`` if at least one pattern in :param:`regex_patterns`
        *matches* :param:`path`; ``False`` otherwise (if none pattern
        match path).
    """
    for pattern in regex_patterns:
        match = pattern.match(str(path))
        if match:
            return True
    return False

match_none = lambda path, regex_patterns: not match_any(path, regex_patterns)


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
            content = file_.read()
    except OSError as error:
        error_msg = (
            f"Unable to open and read ``{node_infos.path}`` file: "
            f"{error.strerror}")

    return FileContent(content, error_msg)


def get_symlink_infos(path, dir_entry=None):
    """Get some infos about a symbolic link.

    Arguments
    ---------
    path : `pathlib.Path`
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


def get_node_content_checksum(node_infos, algorithm=DEFAULT_CHECKSUM_ALGORITHM):
    """Get a MD5 hash hexadecimal digest from content of a node.

    Arguments
    ---------
    node_infos : `NodeInfos`
        Node info whose path will be tried to be red for computing hash.
    algorithm : `str`
        Algorithm to use on node's content to generage checksum.

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

    hash_ = (HASH_FUNCTIONS[algorithm])()
    hash_.update(file_content.content)

    return hash_.hexdigest()


def get_node_infos(path, dir_entry):
    """Get some file tree node informations.

    Arguments
    ---------
    path : `pathlib.Path`
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


def process(parent_dirpath, dir_entry, options):
    """Process some node's path.

    Arguments
    ---------
    parent_dirpath : `pathlib.Path`
        Node's praent path.
    dir_entry : `os.DirEntry`
        Parent's directory entry corresponding to present node, as returned
        by `os.scandir`.
    options : :class:`Options`
        Current application options.

    Returns
    -------
    `NodeInfos` or ``None``
        Node's metadata, or ``None`` if its path has to be excluded as set
        in :param:`options`.
    """
    node_path = parent_dirpath / fsdecode(dir_entry.name)
    if match_any(node_path, options.excluded_regex):
        return None

    node_infos = get_node_infos(node_path, dir_entry)

    if options.checksum:
        algorithm = options.checksum
        node_infos.add_checksum(algorithm,
                                get_node_content_checksum(node_infos, algorithm))

    return node_infos


def walk(path_to_walk, options):
    """Walk a given path.

    Arguments
    ---------
    path_to_walk : `pathlib.Path`
        Directory path to walk.
    optins : :class:`Options`
        Current application options.

    Yields
    ------
    `iterable` of `NodeInfos`
        List of output of shell command `ls` on walked paths as constructed
        `Node` instances.
    """
    LOGGER.info((f"  Start scanning `{options.get_path(path_to_walk)}/` "
                 "directory..."))
    with scandir(path_to_walk) as dir_entries:
        for dir_entry in dir_entries:
            node = process(path_to_walk, dir_entry, options)

            if node:
                yield node
                sleep(options.get_random_sleep_time())

                if node.type == NodeType.directory:
                    yield from walk(node.path, options)


def process_only(node_path, options):
    """Process some terminal node's path.

    Arguments
    ---------
    node_path : `pathlib.Path`
        Node's path to process.
    options : :class:`Options`
        Current application options.

    Returns
    -------
    `NodeInfos` or ``None``
        Node's metadata, or ``None`` if :param:`node_path` has to be excluded
        as set in :param:`options`.
    """
    # Search for `os.DirEntry` corresponding to :param:`node_path`
    parent_dirpath = node_path.parent
    with scandir(parent_dirpath) as dir_entries:
        for dir_entry in dir_entries:
            if dir_entry.name == node_path.name:
                node = process(parent_dirpath, dir_entry, options)
                if node:
                    return node


# CLI  ----------------------------------------------------------------------

def create_args_parser():
    """Create a CLI arguments parser.
    """
    parser = ArgumentParser(description=SCRIPT_DESC)
    parser.add_argument('--exclude', default=",".join(DEFAULT_EXCLUDED),
                        help=("list of paths to exclude from walking, comma "
                              "separated (e.g. 'path1,path2'). In order to "
                              "exclude a directory and all of its children, "
                              "let suffix its path wirth a slash (``/``). "
                              "If paths are relative, they will be resolve "
                              "as relative to path set by "
                              "`--excluded-relative-to` option."))
    parser.add_argument('--excluded-relative-to', default="<WALKED>",
                        help=("Path to which relative ones defined as "
                              "excluded (see `--exclude` option) are relative "
                              "to. Common options are ``<HOME>`` or "
                              "``<WALKED>``, for either user's home or walked "
                              "path (if only one). Default to ``<WALKED>``."))
    parser.add_argument('--sleep',
                        default=(
                            f"{DEFAULT_MIN_TIME_SLEEP:.3f},"
                            f"{DEFAULT_MAX_TIME_SLEEP:.3f}"),
                        help=("Time interval based on which randomly sleep, "
                              "between two concsecutive `ls` commands on "
                              "*file* (not directories). Time values are "
                              "expresses in seconds, with optional decimal "
                              "parts. Default to "
                              f"``{DEFAULT_MIN_TIME_SLEEP:.3f},"
                              f"{DEFAULT_MAX_TIME_SLEEP:.3f}``."
                              "If you explicitely desires no sleep time, "
                              "option must be set to ``0,0``."))
    parser.add_argument('-o', '--output',
                        help=("Output CSV filepath where store results of "
                              "`ls` command traversing files tree. "
                              "If not set, `stdout` will be used instead."))
    parser.add_argument('-l', '--log', nargs='?', const='<OUTPUT>.log',
                        help=("Tell if a log file will be used in addition of "
                              "`sys.stdout` (if option is used as a flag), "
                              "and in which filepath log entries will be "
                              "stored; if no value is set, a path derived from "
                              "value of `output` option will be used."))
    parser.add_argument('--pathes-relative-to', default="<WALKED>",
                        help=("Store walked pathes as relative to some other. "
                              "Common options are ``<HOME>`` or ``<WALKED>``, "
                              "for either user's home or walked path (if only "
                              "one). Script will try to convert pathes if this "
                              "is possible; else, or if option is not set, "
                              "all pathes will be absolute."))
    parser.add_argument('-c', '--checksum', nargs='?', choices=['md5'],
                        default=None, const=DEFAULT_CHECKSUM_ALGORITHM,
                        help=("Set walker to also compute a checksum for each "
                              "encountered file, and which algorithm to use."))
    parser.add_argument('pathes', nargs='*',
                        help="Pathes to walk. If not set, default to `.`.")
    return parser


# Main  ---------------------------------------------------------------------

def configure_logging(logfile_path=None, encoding=ENCODING,
                      level=DEFAULT_LOG_LEVEL):
    """Configure script logging behavior.

    Arguments
    ---------
    logfile_path : `str`
        Path of additional logfile, if any.
    encoding : `str`
        Encoding to use for additional logfile.
    level : `int`
        Minimum logging level for both logger and handlers.
    """
    # Create formatters
    stderr_formatter = \
        Formatter(fmt='%(asctime)s %(levelname)s: %(message)s',
                  datefmt="%H:%M:%S")
    if logfile_path:
        logfile_formatter = \
            Formatter(fmt='%(asctime)s - %(levelname)s - %(message)s',
                      datefmt="%Y-%m-%d %H:%M:%S")

    # Create handlers
    stderr_handler = StreamHandler(stream=stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(stderr_formatter)
    if logfile_path:
        logfile_handler = FileHandler(logfile_path, mode='ta',
                                      encoding=encoding)
        logfile_handler.setLevel(level)
        logfile_handler.setFormatter(logfile_formatter)

    # Configure main loader
    LOGGER.addHandler(stderr_handler)
    if logfile_path:
        LOGGER.addHandler(logfile_handler)
    LOGGER.setLevel(level)


def extend_excluded(excluded, script_path, excluded_relative_to,
                    output_path=None, logfile_path=None):
    """Enhance path excluded list with current script name and optional
    output path.

    Arguments
    ---------
    excluded: `list` of `str`
        Existing list of patterns to exclude.
    script_path : :class:`pathlib.Path`
        This current script absolute path, as called by Python.
    excluded_relative_to : :class:`pathlib.Path`
        Absolute path from which relative excluded one will be relative to.
    output_path : :class:`pathlib.Path`
        CSV output absolute filepath, if any.
    logfile_path : :class:`pathlib.Path`
        Logfile path, if any.

    Returns
    -------
    `list` of regexp. objects
        New list of excluded patterns, enhanced with :param:`script_path` and
        :param:`output_path`.
    """
    _excluded = []

    # Transform any path in absolute path as `str`
    for path in excluded:
        if not path.startswith('/'):
            path = join(excluded_relative_to, path)
        _excluded.append(path)

    # Append current paths to excluded list
    _excluded.append(str(script_path))
    if output_path:
        _excluded.append(str(output_path))
    if logfile_path:
        _excluded.append(str(logfile_path))

    # Transform path in regex objects
    excluded = []
    for path in _excluded:
        is_path_dir = path.endswith('/')
        path = escape(str(Path(path).resolve()))
        if is_path_dir:
            # Exclude both dirpath (without its optional slash) and
            # all of its children. Note that previous leading slash was stripped
            # Path.resolve transformation.
            path += '(/.*)?'
        excluded.append(compile_(f"^{path}$"))

    return excluded


def prepare_options(app_run_infos, parsed_cli_args):
    """Prepare application global options object.

    Arguments
    ---------
    app_run_infos : :class:`AppRunInfos`
        Application's run informations.
    parsed_cli_args : `namespace`
        Parsed CLI arguments, as returned by
        `argparse.ArgumentParser.parse_args()`.

    Returns
    -------
    :class:`Options`
        Application configuration.
    """
    # Sleep time interval
    sleep_option = parsed_cli_args.sleep
    sleep_parts = sleep_option.split(',')
    if len(sleep_parts) != 2:
        exit_on_error((f"`--sleep` option must contain 2 parts separeted by a "
                       "comma (`,`); value passed here, ``{sleep_option}``, "
                       "contains {len(sleep_parts} parts separeted by a comma!"))
    try:
        min_sleep_time, max_sleep_time = float(sleep_parts[0]), float(sleep_parts[1])
    except ValueError as error:
        exit_on_error((f"Unable to parse given `--sleep` time interval in two "
                       f"valid float numbers; passed values were: "
                       f"[{sleep_parts[0]}, {sleep_parts[1]}]."))
    if min_sleep_time > max_sleep_time:
        min_slee_time, max_sleep_time = max_sleep_time, min_sleep_time

    # Pathes to walk
    pathes = ["."] if (parsed_cli_args.pathes is None) \
                   else parsed_cli_args.pathes
    pathes_to_walk = []
    for path in pathes:
        try:
            pathes_to_walk.append(Path(path).resolve(strict=True))
        except FileNotFoundError as error:
            exit_on_error(f"Unable to reach ``{path}`` path to walk on it!")

    # Nodes' pathes relative to
    pathes_relative_to = None
    if ('pathes_relative_to' in parsed_cli_args) \
            and (parsed_cli_args.pathes_relative_to is not None):
        _pathes_relative_to = parsed_cli_args.pathes_relative_to
        if _pathes_relative_to == "<HOME>":
            pathes_relative_to = Path.home()
        elif _pathes_relative_to == "<WALKED>":
            if len(pathes_to_walk) > 1:
                exit_on_error((f"--pathes-relative-to option could only be "
                               f"set to ``<WALKED>`` if there is only one "
                               f"path to be walked; currently, "
                               f"{len(pathes_to_walk)} are set!"))
            pathes_relative_to = pathes_to_walk[0]
        else:
            pathes_relative_to = Path(_pathes_relative_to).resolve()
        if not pathes_relative_to.exists():
            exit_on_error((f"Path, to which others will be relative to, "
                           f"``{pathes_relative_to}`` seems to not exists!"))

    # Manage output
    output_path = None if (('output' not in parsed_cli_args)
                            or (parsed_cli_args.output is None)) \
                       else Path(parsed_cli_args.output).resolve()
    if output_path and Path(output_path).resolve().exists():
        exit_on_error((f"Output filepath ``{output_path}`` already exists: "
                       f"could not write in it!"))

    # Manage additional log, if any
    logfile_path = None if ('log' not in parsed_cli_args) \
                        else parsed_cli_args.log
    if logfile_path == '<OUTPUT>.log':
        if output_path is None:
            exit_on_error((
                f"CLI option `--log` could only be used with no value if "
                f"``--output`` is already set, as value of logfile path will "
                f"be derived from value of output path!"))
        ext_length = len("".join(output_path.suffixes))
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        logfile_path_prefix = str(output_path)[:-ext_length]
        logfile_path = f"{logfile_path_prefix}-{now}.log"
    if logfile_path:
        logfile_path = Path(logfile_path).resolve()
        if logfile_path.exists():
            exit_on_error((f"Additional logfile path ``{logfile_path}`` "
                           f"already exists: could not write in it!"))

    #   Excluded pathes relative to
    _excluded_relative_to = parsed_cli_args.excluded_relative_to
    if _excluded_relative_to == "<HOME>":
        excluded_relative_to = Path.home()
    elif _excluded_relative_to == "<WALKED>":
        if len(pathes_to_walk) > 1:
            exit_on_error((f"--excluded-relative-to option could only be "
                           f"set to ``<WALKED>`` if there is only one path to "
                           f"be walked; currently, {len(pathes_to_walk)} are "
                           f"set!"))
        excluded_relative_to = pathes_to_walk[0]
    else:
        excluded_relative_to = Path(_excluded_relative_to).resolve()

    #   Construct list of pathes to exclude
    excluded = [] if ("exclude" not in parsed_cli_args) \
                    else set(parsed_cli_args.exclude.split(','))
    excluded = extend_excluded(excluded, script_path=app_run_infos.script_path,
                               excluded_relative_to=excluded_relative_to,
                               output_path=output_path,
                               logfile_path=logfile_path)

    # Return application options container
    return Options(parsed_cli_args, pathes_to_walk,
                   min_sleep_time=min_sleep_time, max_sleep_time=max_sleep_time,
                   pathes_relative_to=pathes_relative_to,
                   output_path=output_path, logfile_path=logfile_path,
                   excluded=excluded, excluded_relative_to=excluded_relative_to,
                   checksum=parsed_cli_args.checksum)


def log_infos(app_run_infos, options):
    """Start logging application run options.

    Arguments
    ---------
    app_run_infos : :class:`AppRunInfos`
        Application's run informations.
    options : :class:`Options`
        Application options.
    """
    LOGGER.info("Application run informations:")
    LOGGER.info(f"- current Python script path: {app_run_infos.script_path}")
    LOGGER.info(f"- current working directory: {app_run_infos.working_dirpath}")
    LOGGER.info(f"- process id (pid): {app_run_infos.pid}")
    LOGGER.info(f"- start date/time: {app_run_infos.start_datetime_as_isoformat}")
    LOGGER.info("Will scan following pathes:")
    for path in options.walked_pathes:
        LOGGER.info(f"- `{path}`")
    LOGGER.info("Options are set as following:")
    LOGGER.info((f"- sleep time interval (in s.): "
                 f"[{options.min_sleep_time:.3f}, "
                 f"{options.max_sleep_time:.3f}]"))
    LOGGER.info(f"- checksum algorithm to use, if any: {options.checksum}")
    LOGGER.info(f"- set pathes relative to: `{options.pathes_relative_to}`")
    LOGGER.info(f"- output of scan file: `{options.output_path}`")
    LOGGER.info(f"- additional log file: `{options.logfile_path}`")
    LOGGER.info(f"- set excluded pathes relative to: `{options.excluded_relative_to}`")

    excluded_patterns = options.excluded_patterns
    if len(excluded_patterns) == 0:
        LOGGER.info(f"- excluded path patterns are: []")
    else:
        LOGGER.info(f"- excluded path patterns are:")
        for excluded_pattern in excluded_patterns:
            LOGGER.info(f'  - ``"{excluded_pattern}"``')


def _main(options):
    """Core function of Main function.
    """
    # Walk tree and print dir. entries metadata:
    write_new_line(options.output_path, ENCODING, NodeInfos.colstocsv())

    #   Process node(s)
    pathes_relative_to = options.pathes_relative_to
    output_path = options.output_path

    for path in options.walked_pathes:
        # Case of terminal node (e.g. file):
        if not path.is_dir():
            node_infos = process_only(path, options)
            if node_infos:
                write_new_line(output_path, ENCODING,
                               node_infos.tocsv(pathes_relative_to))
        else:
            # Nominal case of a directory:
            for node_infos in walk(path, options):
                write_new_line(output_path, ENCODING,
                               node_infos.tocsv(pathes_relative_to))

        sleep(options.get_random_sleep_time())


def main():
    """Main function, software entrypoint.
    """
    app_run_infos = AppRunInfos()

    args_parser = create_args_parser()
    parsed_cli_args = args_parser.parse_args()

    # Continue application configuration
    options = prepare_options(app_run_infos, parsed_cli_args)
    configure_logging(options.logfile_path)

    # Start logging
    log_infos(app_run_infos, options)

    # Walk tree and print dir. entries metadata:
    LOGGER.info(f"Start scanning...")

    _main(options)

    # End of run!
    LOGGER.info(f"Stop scanning: job finished!")
    return 0


if __name__ == '__main__':
    exit(main())

