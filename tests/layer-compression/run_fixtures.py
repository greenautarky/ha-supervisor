#!/usr/bin/env python3
"""Fixtures for scripts/check-layer-compression.py (the LIVE script).

Canonical source: greenautarky/ga-core-armv7 (scripts/ and tests/layer-compression/);
the script here is a byte-identical copy.

Captured 2026-11-25 from GHCR (manifest + first 4 bytes of each layer):
must-fail = Core 2026.10.4.5, must-pass = Core 2026.10.4.6, both archs.
The rest is synthetic. Also: every push of an image in build.yaml is followed by
the gate, and two mutants of the live script must turn exactly their case red.
"""

# ruff: noqa: T201, S102  (a fixture runner: prints its verdicts, exec loads the mutants)
from contextlib import redirect_stdout
import importlib.util
import io
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIVE = ROOT / "scripts" / "check-layer-compression.py"
fails: list[str] = []
passed: list[str] = []


def load(text=None):
    """Load the live script, or a mutant of it, as a module."""
    src = LIVE.read_text() if text is None else text
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("c", None))
    exec(compile(src, str(LIVE), "exec"), mod.__dict__)
    return mod


def ok(name, cond, why=""):
    """Record one verdict."""
    (passed if cond else fails).append(name)
    print(f"  {'ok   ' if cond else 'FAIL '} {name} {'' if cond else why}")


def run(mod, data, tag):
    """Run main() of MOD against a fake registry serving DATA."""

    class Fake:
        def __init__(self, repo):
            pass

        def manifest(self, ref):
            if ref not in data["manifests"]:
                raise mod.CannotCheck("404")
            return data["manifests"][ref]

        def head4(self, d):
            if d not in data["heads"]:
                raise mod.CannotCheck("404")
            return bytes.fromhex(data["heads"][d])

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.main([f"r.example/x/y:{tag}"], registry=Fake)
    return rc, out.getvalue()


C = load()
D = {n: "sha256:" + n * 64 for n in "abc"}
GZ, ZS, TAR = "1f8b0800", "28b52ffd", "2e2f0000"
G = "application/vnd.docker.image.rootfs.diff.tar.gzip"
OZ = "application/vnd.oci.image.layer.v1.tar+zstd"
OT = "application/vnd.oci.image.layer.v1.tar"


def img(*layers):
    """Build a manifest with the given (digest key, media type) layers."""
    return {
        "layers": [{"digest": D[d], "mediaType": mt, "size": 1} for d, mt in layers]
    }


for d, want in (("must-fail", 1), ("must-pass", 0)):
    files = sorted((HERE / d).glob("*.json"))
    ok(f"{d}: fixtures for both archs", len(files) == 2, str(files))
    for f in files:
        data = json.loads(f.read_text())
        tag = next(iter(data["manifests"]))
        rc, out = run(C, data, tag)
        ok(
            f"{d}/{f.name}: rc {want}, 27 layers inspected",
            rc == want and ": 27 layers inspected" in out,
            out,
        )
        if want:
            ok(
                f"{d}/{f.name}: names exactly the two zstd layers",
                out.count("declared gzip, is zstd") == 2
                and out.count("::error::") == 2,
                out,
            )

H = {D["a"]: GZ, D["b"]: ZS, D["c"]: TAR}
cases = [
    ("zero layers fails", {"t": {"layers": []}}, H, 1, "zero layers"),
    ("declared zstd + zstd passes", {"t": img(("b", OZ))}, H, 0, "1 layers"),
    ("declared tar + tar passes", {"t": img(("c", OT))}, H, 0, "1 layers"),
    (
        "declared zstd but gzip fails",
        {"t": img(("a", OZ))},
        H,
        1,
        "declared zstd, is gzip",
    ),
    ("declared gzip but plain fails", {"t": img(("c", G))}, H, 1, "is uncompressed"),
    (
        "unknown media type fails",
        {"t": img(("a", "application/x"))},
        H,
        1,
        "unknown media type",
    ),
    ("unreadable blob cannot check", {"t": img(("a", G))}, {}, 2, "could not check"),
    (
        "short blob cannot check",
        {"t": img(("a", G))},
        {D["a"]: "1f8b"},
        2,
        "could not check",
    ),
    ("missing manifest cannot check", {}, H, 2, "could not check"),
    (
        "index: the bad arch is found",
        {
            "t": {
                "manifests": [
                    {
                        "digest": "m1",
                        "platform": {"architecture": "arm64", "os": "linux"},
                    },
                    {
                        "digest": "m2",
                        "platform": {
                            "architecture": "arm",
                            "variant": "v7",
                            "os": "linux",
                        },
                    },
                    {
                        "digest": "att",
                        "platform": {"architecture": "unknown", "os": "unknown"},
                    },
                ]
            },
            "m1": img(("a", G)),
            "m2": img(("a", G), ("b", G)),
        },
        H,
        1,
        "armv7 layer 1",
    ),
    (
        "index of attestations only fails",
        {
            "t": {
                "manifests": [
                    {
                        "digest": "att",
                        "platform": {"architecture": "unknown", "os": "unknown"},
                    }
                ]
            }
        },
        H,
        1,
        "no image",
    ),
]
for name, man, heads, want, text in cases:
    rc, out = run(C, {"manifests": man, "heads": heads}, "t")
    ok(name, rc == want and text in out, f"rc={rc} {out}")
with redirect_stdout(io.StringIO()):
    ok("no argument is not green", C.main([]) == 2)
    ok("a bare repo is not green", C.main(["r.example/x/y"]) == 2)

# wiring: the gate runs after the push step, over every tag that step pushed
wf = (ROOT / ".github/workflows/build-ga-supervisor.yml").read_text()
push = wf.find('            docker push "$t"\n')
rec = wf.find('echo "$t" >> "$RUNNER_TEMP/pushed-tags"')
gate = wf.find('scripts/check-layer-compression.py "${refs[@]}"')
ok(
    "the push step records every tag it pushes",
    push > 0 and push < rec < push + 120,
    f"{push} {rec}",
)
ok("the gate checks the recorded tags, after the push", gate > rec > 0, f"{rec} {gate}")

# cause: no upstream Home Assistant image enters the pushing dockerd
srcs = [wf] + [p.read_text() for p in (ROOT / "scripts").glob("*.sh")]
# An IMAGE argument, not a value (`-e REPO=ghcr.io/...` names a repo, pulls nothing).
UPSTREAM_RUN = re.compile(r"docker (run|pull)\b.*(?<![=\w/])ghcr\.io/home-assistant/")


def upstream_runs(text):
    """Lines that run or pull an upstream Home Assistant image."""
    return [
        ln.strip()
        for ln in text.replace("\\\n", " ").splitlines()
        if UPSTREAM_RUN.search(ln)
    ]


for line, want in (
    ("docker run --rm ghcr.io/home-assistant/base-python:3.14 true", 1),
    ('docker pull "ghcr.io/home-assistant/home-assistant:${V}"', 1),
    (
        'docker run --rm --platform linux/arm64 --entrypoint python3 \\\n  "ghcr.io/home-assistant/home-assistant:x" -m pip',
        1,
    ),
    (
        'docker run -d -e HOMEASSISTANT_REPOSITORY=ghcr.io/home-assistant/qemuarm-homeassistant "$IMAGE"',
        0,
    ),
    ("docker run --rm local/aarch64-hassio-supervisor:scan", 0),
):
    ok(
        f"cause pattern {'flags' if want else 'passes'}: {line[:50]}",
        len(upstream_runs(line)) == want,
    )
pulls = [ln for t in srcs for ln in upstream_runs(t)]
ok(
    "no docker run/pull of a ghcr.io/home-assistant image (workflow + scripts)",
    not pulls,
    str(pulls),
)

# mutants of the LIVE script
src = LIVE.read_text()
bad = json.loads((HERE / "must-fail/core-armv7-2026.10.4.5.json").read_text())
for name, old, new, data, tag in (
    (
        "mutant: trust the media type",
        "elif seen[digest] != want:",
        "elif False:",
        bad,
        "2026.10.4.5",
    ),
    (
        "mutant: zero layers accepted",
        "        if not layers:\n",
        "        if False:\n",
        {
            "manifests": {
                "t": {"manifests": [{"digest": "m1"}, {"digest": "m2"}]},
                "m1": img(("a", G)),
                "m2": {"layers": []},
            },
            "heads": H,
        },
        "t",
    ),
):
    ok(f"{name}: target found", src.count(old) == 1)
    ok(f"{name}: live red", run(C, data, tag)[0] == 1)
    ok(
        f"{name}: mutant goes green, so the guard is what fires",
        run(load(src.replace(old, new)), data, tag)[0] == 0,
    )

ran = len(passed) + len(fails)
print(f"{ran} checks, {len(fails)} failed")
sys.exit(1 if fails or ran < 33 else 0)
