import os
import functools  # type: ignore
from collections import defaultdict
from operator import add
from enum import Enum
import typing
from typing import Any, Callable, Dict, Iterable, List, Set, Tuple, TypeVar

from pydpiper.execution.pipeline import CmdStage


class AutoEnum(Enum):
    """An enumeration that doesn't require you to explicitly give the injection.
    Copied from docs.python.org/3/library/enum.html since apparently it's
    not worth including in the stdlib ..."""
    def __new__(cls):
        value = len(cls.__members__) + 1
        obj = object.__new__(cls)
        obj._value_ = value
        return obj


def NamedTuple(name : str, fields : List[Tuple[str, Callable[[Any], Any]]]):
    """Like typing.NamedTuple, but with some extra functions for (non-destructively) updating fields."""
    F = typing.NamedTuple(name, fields)
    F.replace = F._replace
    F.maybe_replace = lambda self, **kwargs: F._replace(self, **{k:v for (k,v) in kwargs.items() if v is not None})
    return F


T = TypeVar('T')
U = TypeVar('U')


def collect(xs : Iterable[Tuple[T, U]]) -> Dict[T, List[U]]:  # TODO could add 'by=...' instead of just accumulating
    d = defaultdict(list)
    for a, b in xs:
        d[a] += [b]
    return d


def raise_(err: BaseException):
    """`raise` is a keyword and `raise e` isn't an expression, so can't be used freely"""
    raise err


def explode(filename: str) -> Tuple[str, str, str]:
    # TODO should this be a namedtuple instead of a tuple? Probably...can still match against, etc.
    """Split `filename` into a directory, 'base', and extension.
    >>> explode('/path/to/some/file.ext')
    ('/path/to/some', 'file', '.ext')
    >>> explode('./relative_path_to/file.ext')
    ('./relative_path_to', 'file', '.ext')
    >>> explode('file')
    ('', 'file', '')
    """
    base, ext = os.path.splitext(filename)
    directory, name = os.path.split(base)
    return (directory, name, ext)


def pairs(lst: List[T]) -> List[Tuple[T, T]]:
    return list(zip(lst[:-1], lst[1:]))


def flatten(*xs):
    return functools.reduce(add, xs, [])


class NotProvided(object):
    """To be used as a datatype indicating no argument with this name was supplied
    in the situation when None has another sensible meaning, e.g., when a sensible
    default file is available but None indicates *no* file should be used."""


class NoneError(ValueError): pass


def ensure_nonnull_return(f):
    def g(*args):
        result = f(*args)
        if result is None:
            raise NoneError
        else:
            return result

    return g


def output_directories(stages: Set[CmdStage]) -> Set[str]:
    """Directories to be created (currently rather redundant in the presence of subdirectories).
    No need to consider stage inputs - any input already exists or is also the output
    of some previous stage."""
    # What if an output file IS a directory - should there be a special
    # way of tagging this other than ending the path in a trailing "/"?
    all_files_to_consider = []
    for stage in stages:
        all_files_to_consider = all_files_to_consider + stage.outputFiles
        if stage.logFile:
            all_files_to_consider.append(stage.logFile)
        else:
            print("no log file for stage:")
            # old style CmdStage (from execution/pipeline.py) uses __repr__ to print itself
            print(stage)
            raise ValueError("No logfile!!")
    return {os.path.dirname(filename) for filename in all_files_to_consider}

# this doesn't seem possible to do properly ... but we could turn a namespace into another type
# and use that as a default object, say
# def make_parser_and_class(**kwargs):
#    """Create both an argparser and a normal class with the same constraints"""
#    p = argparse.ArgumentParser(**kwargs)
#    fields = [v.... for v in p._option_store_actions.values()]
#    pass #TODO use p._option_string_actions ?

