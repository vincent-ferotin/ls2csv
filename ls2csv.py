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
from errno import ENOENT
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
    strerror,
)
from os.path import (
    getatime,
    getctime,
    getmtime,
    getsize,
    isdir,
    isfile,
    islink,
    join,
    lexists,
)
from pathlib import Path
from random import uniform
from re import (
    compile as compile_,
    escape,
)
from signal import (
    SIGHUP,
    SIGINT,
    #SIGKILL,  # cannot be caught blocked or ignored
    SIGPROF,
    #SIGSTOP,  # cannot be caught blocked or ignored
    SIGTERM,
    SIGTSTP,
    SIGUSR1,
    SIGUSR2,
    signal,
)
from subprocess import (
    CalledProcessError,
    PIPE,
    run,
    STDOUT,
)
from sys import (
    argv,
    exit,
    stderr,
)
from time import sleep


# Constants  ----------------------------------------------------------------

SCRIPT_DESC = "Custom version of `ls` output in results in CSV format."

LOGGER = getLogger(__name__)

APP_RUN_INFOS = None

DEFAULT_LOG_LEVEL = INFO

DEFAULT_CHECKSUM_ALGORITHM = "md5"
HASH_FUNCTIONS = {
    'md5': md5,
}

SIGNALS = {
    SIGHUP:  "SIGHUP",
    SIGINT:  "SIGINT",
    #SIGKILL,  # cannot be caught blocked or ignored
    SIGPROF: "SIGPROF",
    #SIGSTOP,  # cannot be caught blocked or ignored
    SIGTERM: "SIGTERM",
    SIGTSTP: "SIGTSTP",
    SIGUSR1: "SIGUSR1",
    SIGUSR2: "SIGUSR2",
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
    r'(?P<p_type>[-ldrMnpscbD?]{1})'
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
    other = 0
    directory = 1
    file = 2
    symlink = 3
    unknown = 4
    excluded = 5

    def as_ls_output_char(self):
        equivalence = {
            NodeType.file: "-",
            NodeType.directory: "d",
            NodeType.symlink: "l"
        }
        return equivalence.get(self, "?")


@unique
class SymLinkType(Enum):
    other = 0
    directory = 1
    file = 2
    symlink = 3
    unknown = 4
    broken = 5
    circular = 6


@unique
class OwnerType(Enum):
    user = 1
    group = 2
    other = 3


DirEntries = namedtuple('DirEntries', (
    'directories',
    'files',
    'links',
    'excluded',
    'unknowns'))


class Result:
    """Container for either a value (result of function call) or an error.
    """
    def __init__(self, value=None, error_msg=None):
        self._value = value
        self._error_msg = error_msg

    @property
    def value(self):
        return self._value

    @property
    def error_msg(self):
        return self._error_msg

    def has_value(self):
        return self._value is not None

    def has_error(self):
        return self._error_msg is not None


class DirEntry:
    """Wrapper class around :class:`os.DirEntry`.
    """
    def __init__(self, parent_dirpath, entry, options, type_=None):
        self._path = parent_dirpath / fsdecode(entry.name)
        self._entry = entry
        self.set_type(options, type_)

    @property
    def path(self):
        return Path(self._path)

    def is_path_existing(self):
        return lexists(self._path)

    @property
    def name(self):
        return self._entry.name

    @property
    def entry(self):
        return self._entry

    @property
    def type(self):
        if self._type:
            return self._type
        # else: default: unknown
        return NodeType.unknown

    def set_type(self, options, type_=None):
        # Test if node's path must be excluded
        if options.is_path_excluded(self._path):
            self._type = NodeType.excluded
            return
        # else:

        # Set type if any
        if type_:
            self._type = type_
            return
        # else:

        # Try inferring type from dir_entry
        dir_entry = self._entry
        try:
            if dir_entry.is_symlink():
                type_ = NodeType.symlink
            elif dir_entry.is_dir(follow_symlinks=False):
                type_ = NodeType.directory
            elif dir_entry.is_file(follow_symlinks=False):
                type_ = NodeType.file
            else:
                type_ = NodeType.unknown
        except OSError as error:
            type_ = NodeType.unknown

        self._type = type_

    def is_dir(self):
        return self._type == NodeType.directory

    def is_file(self):
        return self._type == NodeType.file

    def is_symlink(self):
        return self._type == NodeType.symlink

    def is_type_unknown(self):
        return self.type == NodeType.unknown

    def is_excluded(self):
        return self._type == NodeType.excluded

    @classmethod
    def new_entry_from_path(cls, path, options):
        """Create a new DirEntry from a given path.

        Arguments
        ---------
        path : `pathlib.Path`
            Absolute and resolved and existing path to transform into
            a new entry.
        options : :class:`Options`
            Current application options.

        Raises
        ------
        `OSError`
            If OS encounters some problem scanning parent directory
            with `os.scandir()`.
        `FileNotFoundError`
            If current path is no more a valid parent's entry.

        Returns
        -------
        :class:`DirEntry`
            New dir. entry corresponding to :param:`path`.
        """
        parent_dirpath = path.parent
        name = path.name
        not_found = False

        try:
            with scandir(parent_dirpath) as dir_entries:
                for dir_entry in dir_entries:
                    if dir_entry.name == name:
                        return cls(parent_dirpath, dir_entry, options)
                not_found = True
        except OSError as error:
            LOGGER.error((
                f"Failed to run `os.scandir()` on ``{parent_dirpath}`` path! "
                f"Get following error message: {error.strerror}"))
            raise error

        # We should normaly never reach this section!
        if not_found:
            error_msg = (
                f"`{name}` was not found amongst parent directory path "
                f"``{parent_dirpath}``! This could occurs if child entry or "
                f"its parent were deleted between the first time child path "
                f"was checked as existing and now, when scanning its parent...")
            # See https://stackoverflow.com/questions/36077266/how-do-i-raise-a-filenotfounderror-properly
            raise FileNotFoundError(ENOENT,
                                    (strerror(ENOENT) + " " + error_msg),
                                    path)


class SymLink:
    """Container for symbolic link infos.
    """
    def __init__(self, type_, linked_path=None, resolved_linked_path=None):
        self._type = type_
        self._linked_path = linked_path
        self._resolved_linked_path = resolved_linked_path

    @property
    def type(self):
        return self._type

    def is_linked_path_dir(self):
        return self._type == SymLinkType.directory

    def is_linked_path_file(self):
        return self._type == SymLinkType.file

    def is_linked_path_symlink(self):
        return self._type == SymLinkType.symlink

    def is_linked_path_unknown(self):
        return self._type == SymLinkType.unknown

    def is_broken(self):
        return self._type == SymLinkType.broken

    def is_circular(self):
        return self._type == SymLinkType.circular

    @property
    def linked_path(self):
        if self._linked_path:
            Path(self._linked_path)
        # else:
        return None

    @property
    def resolved_linked_path(self):
        if self._resolved_linked_path:
            Path(self._resolved_linked_path)
        # else:
        return None


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


class NodeInfos:
    """Metadata about a filesystem node.
    """
    def __init__(self, path, type_,
                 links_nb=None, size=None,
                 perms=None, user_owner=None, group_owner=None, security=None,
                 atime=None, mtime=None, ctime=None,
                 symlink_type=None, symlink_value=None, resolved_symlink_path=None,
                 checksums=None, error_msgs=None, ls_output=None):
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
        self._symlink_type = symlink_type
        self._symlink_value = \
            Path(symlink_value) if isinstance(symlink_value, str) \
                                else symlink_value
        self._resolved_symlink_path = \
            Path(resolved_symlink_path) if isinstance(resolved_symlink_path, str) \
                                        else resolved_symlink_path
        self._checksums = dict()
        if checksums:
            self._checksums.update(checksums)

        self._error_msgs = []
        if error_msgs:
            if isinstance(error_msgs, str):
                self._error_msgs.append(error_msgs)
            else:  # assume iterable
                self._error_msgs.extend([msg for msg in error_msgs])
        self._ls_output = ls_output

    @property
    def path(self):
        if self._path:
            return Path(self._path)
        # else:
        return None

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

    def is_path_existing(self):
        return lexists(self._path)

    @property
    def type(self):
        if self._type:
            return self._type
        # else: default: unknown
        return NodeType.unknown

    @property
    def ls_output(self):
        return self._ls_output

    def is_dir(self):
        return self._type == NodeType.directory

    def is_file(self):
        return self._type == NodeType.file

    def is_symlink(self):
        return self._type == NodeType.symlink

    def is_type_unknown(self):
        return self.type == NodeType.unknown

    def is_excluded(self):
        return self._type == NodeType.excluded

    @property
    def links_nb(self):
        return self._links_nb

    @property
    def size(self):
        return self._size

    @property
    def size_value(self):
        if self._size:
            return self._size.value
        # else:
        return None

    def size_value_converted_to(self, unit=None):
        """Convert a given size in bytes into either human or in a given unit.

        Arguments
        ---------
        unit : `str`
            One of `.UNITS`.
            If set to ``None`` (default), try to operate a conversion like
            a human  would do.

        Returns
        -------
        `str` or ``None``
            Size object representation into :param:`unit` unit.
        """
        if self._size:
            return self._size.convert_to(unit)
        # else:
        return None

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
        if self._mtime:
            return self._mtime.timestamp()
        # else:
        return None

    @property
    def mtime_as_isoformat(self):
        if self._mtime:
            return self._mtime.isoformat(' ')
        # else:
        return None

    @property
    def atime(self):
        return self._atime

    @property
    def atime_as_timestamp(self):
        if self._atime:
            return self._atime.timestamp()
        # else:
        return None

    @property
    def atime_as_isoformat(self):
        if self._atime:
            return self._atime.isoformat(' ')
        # else:
        return None

    @property
    def ctime(self):
        return self._ctime

    @property
    def ctime_as_timestamp(self):
        if self._ctime:
            return self._ctime.timestamp()
        # else:
        return None

    @property
    def ctime_as_isoformat(self):
        if self._ctime:
            return self._ctime.isoformat(' ')
        # else:
        return None

    @property
    def symlink_value(self):
        if self._symlink_value:
            return Path(self._symlink_value)
        # else:
        return None

    def get_symlink_value(self, relative_to=None):
        if relative_to is None:
            return self.symlink_value
        # else:
        if self._symlink_value:
            try:
                return self._symlink_value.relative_to(relative_to)
            except ValueError as error:
                return None
        # else:
        return None

    @property
    def symlink_type(self):
        return self._symlink_type

    @property
    def resolved_symlink_path(self):
        if self._resolved_symlink_path:
            return Path(self._resolved_symlink_path)
        # else:
        return None

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

    def has_error(self):
        return len(self._error_msgs) != 0

    @property
    def error_msgs(self):
        return " | ".join(self._error_msgs)

    def add_error_msg(self, new_error_msg):
        self._error_msgs.append(new_error_msg)

    @property
    def ls_output(self):
        return self._ls_output

    @staticmethod
    def colstocsv():
        return tocsv([
            "Path",
            "Type",
            "Has error(s)",
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
            "Sym.link value",
            "Type of sym.link",
            "Fully resolved sym.link path",
            "MD5 checksum",
            "Error message(s)",
            "Unparsed 'ls' output",
        ])

    def tocsv(self, pathes_relative_to=None):
        return tocsv([
            self.get_path(relative_to=pathes_relative_to),
            self.type,
            None if (not self.has_error()) else "ERROR",
            self.links_nb,
            self.size_value,
            self.size_value_converted_to(),
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
            self.get_symlink_value(relative_to=pathes_relative_to),
            self.symlink_type,
            self.resolved_symlink_path,
            self.get_checksum('md5'),
            self.error_msgs,
            self.ls_output,
        ])


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
        return self._start_datetime.isoformat(' ')


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

    def is_path_excluded(self, path):
        """Tell if a given path match any of all regex patterns.

        Arguments
        ---------
        path : `pathlib.Path`
            Directory or file path to test again list of regexp patterns.

        Returns
        -------
        `bool`
            ``True`` if at least one pattern in :param:`regex_patterns`
            *matches* :param:`path`; ``False`` otherwise (if none pattern
            match path).
        """
        path = str(path)
        for pattern in self._excluded:
            match = pattern.match(path)
            if match:
                return True
        return False


# CSV Functions  ------------------------------------------------------------

def csv_escape(string_value):
    """Escape a string value for CSV encoding.

    Arguments
    ---------
    string_value : `str`
        String value to escape for its encoding into CSV.

    Returns
    -------
    `str`
        Escaped translation of :param:`string_value`.
    """
    return string_value.translate({
                '"': '""',
                })


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
        elif isinstance(item, Enum):
            _row.append(f'"{item.name}"')
        elif isinstance(item, (int, float, bool)):
            _row.append(str(item))
        else:
            item = csv_escape(str(item))
            _row.append(f'"{item}"' if (len(item) > 0) else "")

    return ",".join(_row)


# Files-related Functions  --------------------------------------------------

def read_file_content(path):
    """Read a file content.

    If an error is encountered during file opening or content reading,
    `OSError` is catched and not propagated.

    Arguments
    ---------
    path : `str`
        File path from which try reading content.

    Returns
    -------
    :class:`Result`
        Always, reading content wins or errs.
    """
    content, error_msg = (None,) * 2

    try:
        with open(path, mode='rb') as file_:
            content = file_.read()
    except OSError as error:
        error_msg = \
            f"Unable to open and read ``{path}`` file: {error.strerror}"

    return Result(content, error_msg)


def checksum(content, algorithm=DEFAULT_CHECKSUM_ALGORITHM):
    """Get a checksum hash hexadecimal digest from some content.

    Arguments
    ---------
    content : `binary`
        Some content from which compute checksum.
    algorithm : `str`
        Algorithm to use on node's content to generage checksum.

    Returns
    -------
    `str`
        Hexadecimal hash as checksum of :param:`content` through
        :param:`algorithm` computation.
    """
    hash_ = (HASH_FUNCTIONS[algorithm])()
    hash_.update(content)

    return hash_.hexdigest()


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

    Returns
    -------
    :class:`Result`
        Container indicating if function call encountered some error.
    """
    if content is None:
        return Result(True)
    #else:

    if filepath is None:
        print(content)
        return Result(True)
    #else:

    try:
        with open(filepath, mode='at', encoding=encoding) as file_:
            file_.write(content + "\n")
    except OSError as error:
        error_msg = (
            f"Unable to write at end of `{filepath}`; got following error: "
            f"{error.strerror}")
        LOGGER.critical(error_msg)
        return Result(error_msg=error_msg)

    return Result(True)


# Nodes scanning Functions  -------------------------------------------------

def get_symlink_infos(dir_entry):
    """Get some infos about a symbolic link.

    Arguments
    ---------
    dir_entry : :class:`DirEntry`
        Item resulting of `_scandir()` on parent path, if any.

    Returns
    -------
    :class:`Result` whose :attr:`value` could be a :class:`SymLink`
        :param:`path` links informations.
    """
    try:
        linked_path = Path(readlink(dir_entry.path))
    except PermissionError as error:
        error_msg = (
            f"Sym.Link ``{path}`` is unreadable, asking for its actual linked "
            f"path results in a permission error: {error.strerror}")
        return Result(error_msg=error_msg)
    except OSError as error:
        error_msg = (
            f"Sym.Link ``{path}`` is unreadable, asking for its actual linked "
            f"path results in a OSError: {error.strerror}")
        return Result(error_msg=error_msg)

    # Make linked path absolute
    if not linked_path.is_absolute():
        linked_path = dir_entry.path.parent / linked_path

    # Try fully resolve linked path
    linked_abspath = linked_path.resolve()
    resolved_linked_path = None
    try:
        resolved_linked_path = linked_abspath.resolve(strict=True)
    except FileNotFoundError as error:
        return Result(SymLink(SymLinkType.broken, linked_abspath))
    except RuntimeError as error:
        return Result(SymLink(SymLinkType.circular, linked_abspath))

    # Nominal cases:
    error_msg = None
    try:
        if linked_abspath.is_symlink():
            type_ = SymLinkType.symlink
        elif linked_abspath.is_dir():
            type_ = SymLinkType.directory
        elif linked_abspath.is_file():
            type_ = SymLinkType.file
        else:
            type_ = SymLinkType.unkown
    except PermissionError as error:
        error_msg = (
            f"Access to linked path ``{linked_abspath}`` is unreachable, "
            f"trying to dertermine node's type results in a permission error: "
            f"{error.strerror}")
    except OSError as error:
        error_msg = (
            f"Access to linked path ``{linked_path}`` is unreachable, trying "
            f"to dertermine node's type results in an OSerror: "
            f"{error.strerror}")

    return Result(SymLink(type_, linked_abspath, resolved_linked_path))


def get_node_infos(dir_entry):
    """Get some file tree node informations.

    Arguments
    ---------
    dir_entry : :class:`DirEntry`
        Item resulting of `_scandir()` on parent path.

    Returns
    -------
    NodeInfos
        Informations about node located at :param:`path`.
    """
    symlink_value, symlink_type, resolved_symlink_path = (None,) * 3
    error_msgs = []
    path, type_ = dir_entry.path, dir_entry.type

    # Ensure node already exists
    _error_msg = (
        f"Path ``{path}`` seems to either not exist (any more) or user who "
        f"launch application has not enough rights to ask for more "
        f"information about node!")
    try:
        if not dir_entry.is_path_existing():
            error_msgs.append(_error_msg)
    except PermissionError as error:
        error_msgs.append(_error_msg)

    # Sym.link
    if dir_entry.is_symlink():
        symlink_result = get_symlink_infos(dir_entry)
        if symlink_result.has_error():
            error_msgs.append(symlink_result.error_msg)
        if symlink_result.has_value():
            symlink = symlink_result.value
            symlink_value, symlink_type, resolved_symlink_path = \
                symlink.linked_path, symlink.type, symlink.resolved_linked_path

            if symlink.is_broken():
                error_msg = (
                    f"Sym.link is broken: path to ``{symlink.linked_path}`` "
                    f"seems unreachable!")
                error_msgs.append(error_msg)
            elif symlink.is_circular():
                error_msg = (
                    f"Sym.link could not be resolved, as "
                    f"``{symlink.linked_path}`` is a start of circular "
                    f"reference!")
                error_msgs.append(error_msg)
            elif symlink.is_linked_path_unknown():
                error_msg = (
                    f"Sym.link value of ``{symlink.linked_path}`` could not "
                    f"be resolved to either a file, a directory or an other "
                    f"sym.link!")
                error_msgs.append(error_msg)

    # `ls` shell command
    ls_output, _ls_output, \
    p_type, perms, links_nb, user_owner, \
        group_owner, security, size = (None,) * 9
    ls_cmd = LSD.format(path=path) if dir_entry.is_dir() \
                                   else LS.format(path=path)

    try:
        completed_process = run(ls_cmd, shell=True, check=True, stdout=PIPE,
                                stderr=STDOUT, encoding=ENCODING)
        _ls_output = completed_process.stdout.rstrip()
    except CalledProcessError as error:
        error_msgs.append(f"Failed to run `ls` on ``{path}``: {error.stderr}")

    if _ls_output:
        match = LS_OUTPUT_REGEX.match(_ls_output)
        if match is None:
            ls_output = _ls_output
            error_msg = \
                f"Unable to parse output of `ls` command: ``{ls_output}``!"
            error_msgs.append(error_msg)
        else:
            p_type, perms, links_nb, \
                user_owner, group_owner, \
                security, size = match.group('p_type', 'p_perms', 'links_nb',
                                             'user_owner', 'group_owner',
                                             'security', 'size')
            # Conversions from string to specific types
            links_nb = int(links_nb)
            size = int(size)

            # Check if types are coherents
            if p_type != type_.as_ls_output_char():
                error_msg = (
                    f"Types differ from Python :mod:`os.scandir` result and "
                    f"output of `ls` command: ``{type_}`` (Python) != "
                    f"``{p_type}`` (ls)!")
                error_msgs.append(error_msg)

    # Get (a|m|c)time(s)
    atime, mtime, ctime = (None,) * 3
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
                     symlink_type=symlink_type, symlink_value=symlink_value,
                     resolved_symlink_path=resolved_symlink_path,
                     error_msgs=error_msgs, ls_output=ls_output)


def get_node_content_checksum(node_infos, algorithm=DEFAULT_CHECKSUM_ALGORITHM):
    """Get a checksum hash hexadecimal digest from content of a node.

    Arguments
    ---------
    node_infos : `NodeInfos`
        Node info whose path will be tried to be red for computing hash.
    algorithm : `str`
        Algorithm to use on node's content to generage checksum.

    Preconditions
    -------------
    node_infos
        Node must be of type 'file'.

    Returns
    -------
    :class:`Result` with value as `str`
        MD5 sum hash as hexadecimal digest if node is an actual file and
        reading its content was possible; ``None`` otherwise.
    """
    if not node_infos.is_path_existing():
        error_msg = (
            f"Node's filepath ``{node_infos.path}`` does not point to a valid "
            f"file anymore (detected when trying to compute its checksum)!")
        return Result(error_msg=error_msg)
    # else:

    file_content_result = read_file_content(node_infos.path)
    if file_content_result.has_error():
        return Result(error_msg=file_content_result.error_msg)
    # else:

    return Result(checksum(file_content_result.value, algorithm))


def process_dir_entry(dir_entry, options):
    """Process some node's path.

    Arguments
    ---------
    dir_entry : :class:`DirEntry`
        Directory entry to process.
    options : :class:`Options`
        Current application options.

    Preconditions
    -------------
    dir_entry
        ``not dir_entry.is_excluded()``

    Returns
    -------
    `NodeInfos` or ``None``
        Node's metadata, or ``None`` if its path has to be excluded as set
        in :param:`options`.
    """
    node_infos = get_node_infos(dir_entry)

    algorithm = options.checksum
    if algorithm and node_infos.is_file():
        checksum_result = get_node_content_checksum(node_infos, algorithm)
        if checksum_result.has_error():
            node_infos.add_error_msg(checksum_result.error_msg)
        else:
            node_infos.add_checksum(algorithm, checksum_result.value)

    return node_infos


def _scandir(dir_entry, options):
    """Scan a given directory and sort its entries by types and names.

    Arguments
    ---------
    dir_entry : :class:`DirEntry`
        Directory entry corresponding to a directory to scan.
    options : :class:`Options`
        Current application options.

    Returns
    -------
    :class:`DirEntries`
        Directory entries, as :class:`DirEntry`, sorted by types and names.
    """
    dirs, files, links, excluded, unknowns = ([], [], [], [], [])
    parent_dirpath = dir_entry.path

    try:
        with scandir(parent_dirpath) as dir_entries:
            for dir_entry in dir_entries:
                dir_entry = DirEntry(parent_dirpath, dir_entry, options)

                if dir_entry.is_excluded():
                    excluded.append(dir_entry)
                elif dir_entry.is_symlink():
                    links.append(dir_entry)
                elif dir_entry.is_dir():
                    dirs.append(dir_entry)
                elif dir_entry.is_file():
                    files.append(dir_entry)
                elif dir_entry.is_type_unknown():
                    unknowns.append(dir_entry)
                else:
                    dir_entry.set_type(NodeType.unknown)
                    unknowns.append(dir_entry)
    except OSError as error:
        LOGGER.error((
            f"Failed to run `os.scandir()` on ``{parent_dirpath}`` path! Get "
            f"following error message: {error.strerror}"))
        pass

    dirs = sorted(dirs, key=lambda entry: entry.name)
    files = sorted(files, key=lambda entry: entry.name)
    links = sorted(links, key=lambda entry: entry.name)
    # No need to sort excluded entries!
    unknowns = sorted(unknowns, key=lambda entry: entry.name)

    return DirEntries(dirs, files, links, excluded, unknowns)


def walk(dir_entry, options):
    """Walk a given directory.

    Entries in path are walked in this order:

    #.  by their types first: directories first, then regular files,
        then symlinks, then excluded entries and last unknown typed entries;
    #.  by their names then.

    Arguments
    ---------
    dir_entry : :class:`DirEntry`
        Directory entry corresponding to a directory.
    options : :class:`Options`
        Current application options.

    Yields
    ------
    `iterable` of `NodeInfos`
        List of output of shell command `ls` on walked paths as constructed
        `Node` instances.
    """
    # Ensure current dir. path is not to be excluded before processing it
    if dir_entry.is_excluded():
        LOGGER.info((f"  Not scanning ``{options.get_path(dir_path)}/``: "
                     f"directory path has to be excluded..."))
        return []
    # else:

    LOGGER.info((f"  Start scanning ``{options.get_path(dir_entry.path)}/`` "
                 "directory..."))

    _dir_entries = _scandir(dir_entry, options)
    dir_entries, file_entries, link_entries, \
    excluded_entries, unknown_entries = \
        _dir_entries.directories, _dir_entries.files, _dir_entries.links, \
        _dir_entries.excluded, _dir_entries.unknowns

    # Scan directories first
    subdir_entries = []
    for dir_entry in dir_entries:
        node = process_dir_entry(dir_entry, options)
        subdir_entries.append(dir_entry)
        yield node
        sleep(options.get_random_sleep_time())

    # Scan files second, then symlinks
    for node_entry in (file_entries + link_entries):
        yield process_dir_entry(node_entry, options)
        sleep(options.get_random_sleep_time())

    # Log and yield each of excluded entries
    for dir_entry in excluded_entries:
        LOGGER.info((f"  Not asking more infos about "
                     f"``{options.get_path(dir_entry.path)}``: path has to be "
                     f"excluded..."))
        yield NodeInfos(dir_entry.path, dir_entry.type)

    # Log and yield all unknown entries ?!
    for dir_entry in unknown_entries:
        LOGGER.warning((f"  Node at ``{options.get_path(dir_entry.path)}`` "
                        f"path could create some problems: its type could not "
                        f"be asked by `os.DirEntry.is_*()` methods, and so is "
                        f"currently unkonw..."))
        yield process_dir_entry(dir_entry, options)
        sleep(options.get_random_sleep_time())

    # Last, recursively walk inside each subdirectory nodes
    for subdir_entry in subdir_entries:
        yield from walk(subdir_entry, options)


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
                              "If you explicitely desire no sleep time, "
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

def exit_on_error(msg, return_code=1):
    """Exit system on error.

    .. note::

        This function should only be used *before* application logging is
        fully configured, i.e. only used by :func:`prepare_options`!
        All other cases should see use of :func:`app_exit` instead.

    Arguments
    ---------
    msg : `str`
        Error message to print to `sys.stderr`.
    return_code : `int`
        Return code.
    """
    print(msg, file=stderr)
    exit(return_code)


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


def prepare_options(parsed_cli_args):
    """Prepare application global options object.

    Arguments
    ---------
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
        # FIXME: exit_on_error() ??
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
            exit_on_error(f"Unable to reach ``{path}`` path to scan!")

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
    excluded = extend_excluded(excluded, script_path=APP_RUN_INFOS.script_path,
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


def log_infos(options):
    """Start logging application run options.

    Arguments
    ---------
    options : :class:`Options`
        Application options.
    """
    LOGGER.info("Application run informations:")
    LOGGER.info(f"- current Python script path: {APP_RUN_INFOS.script_path}")
    LOGGER.info(f"- current working directory: {APP_RUN_INFOS.working_dirpath}")
    LOGGER.info(f"- process id (pid): {APP_RUN_INFOS.pid}")
    LOGGER.info(f"- start date/time: {APP_RUN_INFOS.start_datetime_as_isoformat}")
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


def app_exit(return_code=0):
    """Exit current application run.
    """
    now = datetime.now()
    timedelta = now - APP_RUN_INFOS.start_datetime

    LOGGER.critical(f"Process terminating at {now.isoformat(' ')}!")
    LOGGER.info((
        f"Script has run {timedelta} from "
        f"{APP_RUN_INFOS.start_datetime_as_isoformat}!"))

    exit(return_code)


def stop_signal_handler(signal=None, frame=None):
    """Handler for all signals terminating current application run.
    """
    LOGGER.critical((f"Process receiving ``{signal}`` (i.e. "
                     f"``{SIGNALS[signal]}``) signal (or keyboard Ctrl+C "
                     f"interrupt)!"))
    app_exit(2)


def _main(options):
    """Core function of Main function.
    """
    # Register stop signal handler
    for _signal in SIGNALS.keys():
        signal(_signal, stop_signal_handler)

    # Walk tree and print dir. entries metadata:
    write_result = write_new_line(options.output_path, ENCODING,
                                  NodeInfos.colstocsv())
    if write_result.has_error():
        app_exit(1)

    #   Process node(s)
    pathes_relative_to = options.pathes_relative_to
    output_path = options.output_path

    try:
        for path in options.walked_pathes:
            try:
                dir_entry = DirEntry.new_entry_from_path(path, options)
            except FileNotFoundError as error:
                LOGGER.error(error.strerror)
                continue
            # else:

            if dir_entry.is_excluded():
                error_msg = (
                    f"  Not scanning ``{options.get_path(dir_entry.path)}``: "
                    f"node's path has to be excluded...")
                LOGGER.info(error_msg)
                continue
            # else:

            if not dir_entry.is_dir():
                node_infos = process_dir_entry(dir_entry, options)
                write_result = write_new_line(output_path, ENCODING,
                                              node_infos.tocsv(pathes_relative_to))
                if write_result.has_error():
                    app_exit(1)
            else:  # dir_entry.is_dir()
                # Nominal case of a directory:
                for node_infos in walk(dir_entry, options):
                    write_result = write_new_line(output_path, ENCODING,
                                                  node_infos.tocsv(pathes_relative_to))
                    if write_result.has_error():
                        app_exit(1)

            sleep(options.get_random_sleep_time())
    except KeyboardInterrupt as error:
        stop_signal_handler()


def main():
    """Main function, software entrypoint.
    """
    global APP_RUN_INFOS
    APP_RUN_INFOS = AppRunInfos()

    args_parser = create_args_parser()
    parsed_cli_args = args_parser.parse_args()

    # Continue application configuration
    options = prepare_options(parsed_cli_args)
    configure_logging(options.logfile_path)

    # Start logging
    log_infos(options)

    # Walk tree and print dir. entries metadata:
    LOGGER.info(f"Start scanning...")

    _main(options)

    # End of run!
    LOGGER.critical("Stop scanning: job finished normally!")
    app_exit(0)


if __name__ == '__main__':
    main()

