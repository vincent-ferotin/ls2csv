#!/usr/bin/env python3.7
# -*- -coding: utf-8 -*-
"""Custom version of `ls`.
"""
from argparse import ArgumentParser
from pathlib import Path
from random import uniform
from time import sleep
from sys import (
    argv,
    exit,
)


# Constants  ----------------------------------------------------------------

SCRIPT_DESC = "Script doing nothing except sleeping and printing in a file how much it slept."

MIN_TIME = 1
MAX_TIME = 5

ENCODING="utf-8"


# Classes  ------------------------------------------------------------------

class Sleeper:
    def __init__(self, time=None):
        self._time = 0.
        self._n = 0
        if time:
            self._time = time
            self._n = 1
        self._total = self._time

    @property
    def n(self):
        return self._n

    @property
    def total(self):
        return self._total

    def sleep(self, time):
        self._n += 1
        self._total += time


# Functions  ----------------------------------------------------------------

def write_new_line(filepath=None, encoding=ENCODING, content=None):
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

    with open(filepath, mode='wt', encoding=encoding) as file_:
        file_.write(content + "\n")


def sleep4ever(sleeper, min_time=MIN_TIME, max_time=MAX_TIME):
    while True:
        time_to_sleep = uniform(min_time, max_time)
        sleeper.sleep(time_to_sleep)

        sleep(time_to_sleep)
        yield True


# CLI  ----------------------------------------------------------------------

def create_args_parser():
    """Create a CLI arguments parser.
    """
    parser = ArgumentParser(description=SCRIPT_DESC)
    parser.add_argument('-o', '--output',
                        help="Output text file where storing sleep time.")
    return parser


# Main  ---------------------------------------------------------------------

def main():
    """Main function, software entrypoint.
    """
    args_parser = create_args_parser()
    args = args_parser.parse_args()

    # Parse and adjust options
    #   Manage output
    output_path = None if (('output' not in args) or (args.output is None)) \
                       else Path(args.output).resolve()
    if output_path and Path(output_path).resolve().exists():
        error_msg = (
            f"Output filepath ``{output_path}`` already exists: could not "
            "write in it!")
        exit(error_msg)

    # Start sleeper and sleeping
    sleeper = Sleeper()
    for signal in sleep4ever(sleeper):
        if signal:
            line = (
                f"Sleeping {sleeper.n} times for a total of "
                f"{sleeper.total:.1f} seconds.")
            write_new_line(output_path, ENCODING, line)
        else:
            exit("Error while looping!")

    return 0


if __name__ == '__main__':
    exit(main())

