`ls2csv` -- dumb Python module collecting metadata about a files tree into CSV output
=====================================================================================

Description
-----------

Present project was created for retrieving some metadata about files tree's nodes
and store collected values into a [CSV] output file, and notably it allows
some tweaks on tree walking, such as waiting between two consecutive nodes
analysis, or excluding some paths from a set of patterns.


Status
------

Currently, project only consists on a single module/script, barely good enough
for author's original first use.
**Be aware that it is provided "as is", without any warranty, notably of
correctness -- and it is currently known to have bugs!**


Platform and requirements
-------------------------

Project's software is written in [Python], especially *3.7* version of [CPython],
running on some [Linux O.S. distribution], and originally targets a [Debian]'s
one.
It only depends on [Python's standard library].


Features
--------

-   output collected metadata into a [CSV] file
    -   optional MD5 hexadecimal digest computation of files' contents
    -   sizes in bytes and for human reading
    -   datetimes in both timestamps and ISO format
    -   report of all errors encountered during node's analysis
-   paths patterns exclusion
    -   automatically adding script it-self and outputs to exclusion list
-   setting collected paths relative to another
-   configurable sleep time between two consecutive nodes analysis
-   logging to both `stderr` and file
-   gracefully handle interrupt signal send to it
-   allowing walked tree subparts deletion while running
    (but could do nothing for additions between directory scan and analysis
    of each of its nodes)


Usage
-----

Script is intended to be called directly by [CPython] through shell command-line:

```sh
$ python3.7 ls2csv.py [options] <pathes>
```

To see all allowed options and arguments, use `--help` option:

```sh
$ python3.7 ls2csv.py --help
```


[CPython]:                      https://en.wikipedia.org/wiki/CPython
[CSV]:                          https://en.wikipedia.org/wiki/Comma-separated_values
[Debian]:                       https://www.debian.org/
[Linux O.S. distribution]:      https://en.wikipedia.org/wiki/Linux_distribution
[Python]:                       https://www.python.org/
[Python's standard library]:    https://docs.python.org/3.7/library/

