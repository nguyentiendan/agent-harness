"""Versioned, detached storage configuration; never stat Docker-host paths here."""


def validate(binding):
    mode, path, revision = (binding.get(k) for k in ("mode", "local_path", "revision"))
    if type(revision) is not int or revision < 0:
        raise ValueError("Invalid workspace revision.")
    if mode == "managed":
        if path is not None:
            raise ValueError("Managed workspace cannot have a local path.")
    elif mode == "local":
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
        ):
            raise ValueError(
                "Use an absolute POSIX folder path without control characters."
            )
    else:
        raise ValueError("Invalid workspace mode.")
    return dict(mode=mode, local_path=path, revision=revision)


def snapshot(scope):
    return validate(
        dict(
            mode=scope.workspace_mode,
            local_path=scope.local_path,
            revision=scope.workspace_revision,
        )
    )
