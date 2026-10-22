"""Versioned, detached storage configuration; never stat Docker-host paths here."""


def validate_binding(binding):
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


def mount_options(binding, volume_name, readonly=False):
    from docker.types import Mount

    binding = validate_binding(binding)
    if binding["mode"] == "local":
        # --mount semantics reject absent sources; -v semantics would create them.
        return {
            "mounts": [
                Mount(
                    target="/workspace",
                    source=binding["local_path"],
                    type="bind",
                    read_only=readonly,
                )
            ]
        }
    return {
        "volumes": {
            volume_name: {"bind": "/workspace", "mode": "ro" if readonly else "rw"}
        }
    }


def profile():
    from docker.types import LogConfig

    return dict(
        user="10001:10001",
        working_dir="/workspace",
        network_mode="none",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="1g",
        memswap_limit="1g",
        nano_cpus=1000000000,
        pids_limit=128,
        tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
        environment={"HOME": "/tmp", "LANG": "C.UTF-8"},
        log_config=LogConfig(type="none"),
        init=True,
    )


def probe_local(client, image, binding, labels=None, name=None):
    import time

    binding = validate_binding(binding)
    if binding["mode"] == "managed":
        return {"ready": True}
    container = None
    program = "import os,tempfile\nassert os.path.isdir('/workspace')\nwith os.scandir('/workspace') as entries: next(entries,None)\np=None\ntry:\n fd,p=tempfile.mkstemp(prefix='.harness-probe-',dir='/workspace')\n with os.fdopen(fd,'wb') as f: f.write(b'probe'); f.flush()\n with open(p,'rb') as f: assert f.read()==b'probe'\nfinally:\n if p: os.unlink(p)\n"
    try:
        container = client.containers.run(
            image,
            ["python3", "-I", "-c", program],
            detach=True,
            **({"name": name} if name else {}),
            labels={
                **(labels or {}),
                "helper": "true",
                "expires": str(time.time() + 35),
            },
            **profile(),
            **mount_options(binding, "", False),
        )
        if container.wait(timeout=30)["StatusCode"] != 0:
            raise ValueError(
                "Folder must be an existing directory readable and writable by the sandbox user (10001:10001)."
            )
    finally:
        if container:
            from docker.errors import NotFound

            try:
                container.remove(force=True)
            except NotFound:
                pass
    return {"ready": True}
