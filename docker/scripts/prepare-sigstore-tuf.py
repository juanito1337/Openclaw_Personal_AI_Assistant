#!/usr/bin/env python3
"""Stage bounded untrusted TUF transport bytes; Cosign must validate all trust."""

import json
import os
import re
import stat
import sys

MAX_ENTRIES = 2048
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")
METADATA = re.compile(r"(?:[1-9][0-9]*\.)?(root|snapshot|targets)\.json\Z")


def open_directory(path):
    """Pin each absolute path component without following any symlink."""
    parts = path.split("/")
    if not path.startswith("/") or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ValueError("unsafe-path")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def stage_repository(source, destination):
    """Copy to an existing private empty directory; discard it on any failure."""
    if source == destination or destination.startswith(source + "/"):
        raise ValueError("overlapping-paths")
    counts = {"files": 0, "directories": 0, "bytes": 0}
    metadata = set()

    def copy_tree(source_fd, destination_fd, depth=0):
        if depth > 32:
            raise ValueError("repository-depth")
        with os.scandir(source_fd) as entries:
            for entry in entries:
                if not NAME.fullmatch(entry.name) or counts["files"] + counts["directories"] >= MAX_ENTRIES:
                    raise ValueError("repository-entry")
                before = entry.stat(follow_symlinks=False)
                directory = stat.S_ISDIR(before.st_mode)
                if not directory and not stat.S_ISREG(before.st_mode):
                    raise ValueError("repository-file-type")
                flags = DIRECTORY_FLAGS if directory else os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                descriptor = os.open(entry.name, flags, dir_fd=source_fd)
                try:
                    info = os.fstat(descriptor)
                    if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                        raise ValueError("repository-changed")
                    if directory:
                        counts["directories"] += 1
                        os.mkdir(entry.name, mode=0o700, dir_fd=destination_fd)
                        child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=destination_fd)
                        try:
                            copy_tree(descriptor, child, depth + 1)
                        finally:
                            os.close(child)
                    else:
                        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                            raise ValueError("repository-file-size")
                        counts["files"] += 1
                        output = os.open(entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=destination_fd)
                        size = 0
                        with os.fdopen(output, "wb") as handle:
                            while block := os.read(descriptor, 64 * 1024):
                                size += len(block)
                                if size > MAX_FILE_BYTES or counts["bytes"] + len(block) > MAX_BYTES:
                                    raise ValueError("repository-size")
                                handle.write(block)
                                counts["bytes"] += len(block)
                        if depth == 0:
                            match = METADATA.fullmatch(entry.name)
                            if match:
                                metadata.add(match[1])
                            elif entry.name == "timestamp.json":
                                metadata.add("timestamp")
                finally:
                    os.close(descriptor)

    source_fd = open_directory(source)
    try:
        destination_fd = open_directory(destination)
        try:
            info = os.fstat(destination_fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("destination-not-private")
            with os.scandir(destination_fd) as entries:
                if next(entries, None) is not None:
                    raise ValueError("destination-not-empty")
            copy_tree(source_fd, destination_fd)
            if metadata != {"root", "timestamp", "snapshot", "targets"}:
                raise ValueError("metadata-missing")
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    return counts


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("arguments")
        print(json.dumps(stage_repository(sys.argv[1], sys.argv[2]), sort_keys=True))
    except (OSError, ValueError):
        print("tuf-repository-invalid", file=sys.stderr)
        sys.exit(1)
