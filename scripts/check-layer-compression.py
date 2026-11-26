#!/usr/bin/env python3
"""Every layer of a PUBLISHED image must really be what its media type says.

  check-layer-compression.py <registry>/<repo>:<tag|@digest> [...]

Core 2026.10.4.4 and .4.5 were published with a zstd blob (the empty layer)
under a tar.gzip media type. `docker push` re-uses a blob dockerd knows for the
same diff_id from an earlier pull (there: the upstream Core image, whose layers
are zstd) and always writes schema2 tar.gzip. Docker's pull sniffs the content
and copes; a consumer that trusts the media type does not.

Reads manifests and the first 4 bytes of each layer blob (Range request), for
every platform of an index. Prints how many layers were inspected.
Exit 0 = all match, 1 = mismatch or unusable image (zero layers, unknown media
type), 2 = could not check (network, auth). Never green on "could not look".
Auth: anonymous, or REGISTRY_USER + REGISTRY_TOKEN.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GZIP, ZSTD = b"\x1f\x8b", b"\x28\xb5\x2f\xfd"
# Pinned. Anything else is unknown and fails.
DECLARED = {
    "application/vnd.docker.image.rootfs.diff.tar.gzip": "gzip",
    "application/vnd.oci.image.layer.v1.tar+gzip": "gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd": "zstd",
    "application/vnd.oci.image.layer.v1.tar": "uncompressed",
    "application/vnd.docker.image.rootfs.diff.tar": "uncompressed",
}
INDEX = ("application/vnd.oci.image.index.v1+json",
         "application/vnd.docker.distribution.manifest.list.v2+json")
MANIFEST = ("application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json")


class CannotCheck(Exception):
    pass


def sniff(head: bytes) -> str:
    if head[:2] == GZIP:
        return "gzip"
    if head[:4] == ZSTD:
        return "zstd"
    return "uncompressed"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Registry:
    """Read-only registry client. `repo` is host/path."""

    def __init__(self, repo):
        self.host, _, self.path = repo.partition("/")
        self._open = urllib.request.build_opener(_NoRedirect).open
        self._token = None

    def _auth(self):
        if self._token is None:
            q = urllib.parse.urlencode({"service": self.host, "scope": f"repository:{self.path}:pull"})
            req = urllib.request.Request(f"https://{self.host}/token?{q}")
            user, tok = os.environ.get("REGISTRY_USER"), os.environ.get("REGISTRY_TOKEN")
            if user and tok:
                req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{tok}".encode()).decode())
            self._token = json.load(urllib.request.urlopen(req, timeout=30))["token"]
        return "Bearer " + self._token

    def _get(self, path, headers, limit=None):
        err = None
        for attempt in range(4):
            try:
                url = f"https://{self.host}/v2/{self.path}/{path}"
                try:
                    r = self._open(urllib.request.Request(
                        url, headers={**headers, "Authorization": self._auth()}), timeout=30)
                except urllib.error.HTTPError as e:
                    if e.code not in (301, 302, 303, 307, 308):
                        raise
                    # the blob store must not get the registry's bearer token
                    r = urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers=headers), timeout=30)
                return r.read(limit) if limit else r.read()
            except urllib.error.HTTPError as e:
                err = e
                if e.code in (400, 401, 403, 404):
                    break
            except (OSError, ValueError, KeyError) as e:
                err = e
            time.sleep(3 * (attempt + 1))
        raise CannotCheck(f"{path}: {err}")

    def manifest(self, ref):
        return json.loads(self._get(f"manifests/{ref}", {"Accept": ", ".join(INDEX + MANIFEST)}))

    def head4(self, digest):
        return self._get(f"blobs/{digest}", {"Range": "bytes=0-3"}, limit=4)


def check_image(reg, ref):
    """-> (inspected, problems). Raises CannotCheck."""
    top = reg.manifest(ref)
    todo, problems, inspected = [], [], 0
    if "manifests" in top:
        for m in top["manifests"]:
            p = m.get("platform") or {}
            if p.get("architecture") == "unknown" and p.get("os") == "unknown":
                continue  # build attestation, not an image
            todo.append((f"{p.get('architecture', '?')}{p.get('variant', '')}", reg.manifest(m["digest"])))
    else:
        todo.append(("image", top))
    if not todo:
        problems.append("no image in the index")
    seen = {}
    for name, m in todo:
        layers = m.get("layers") or []
        if not layers:
            problems.append(f"{name}: zero layers")
        for i, layer in enumerate(layers):
            digest, mt = layer["digest"], layer.get("mediaType", "")
            want = DECLARED.get(mt)
            if digest not in seen:
                head = reg.head4(digest)
                if len(head) < 4:
                    raise CannotCheck(f"{name} layer {i} {digest}: only {len(head)} bytes")
                seen[digest] = sniff(head)
            inspected += 1
            if want is None:
                problems.append(f"{name} layer {i} {digest[:19]}: unknown media type '{mt}'")
            elif seen[digest] != want:
                problems.append(f"{name} layer {i} {digest[:19]} ({layer.get('size')} B): "
                                f"declared {want}, is {seen[digest]}")
    return inspected, problems


def main(refs, registry=Registry):
    if not refs:
        print(__doc__)
        return 2
    rc = 0
    for full in refs:
        m = re.fullmatch(r"([^@\s]+?)(?:@(sha256:[0-9a-f]{64})|:([\w][\w.-]*))", full)
        if not m:
            print(f"::error::'{full}' is not <repo>:<tag> or <repo>@<digest>")
            return 2
        try:
            n, problems = check_image(registry(m[1]), m[2] or m[3])
        except CannotCheck as e:
            print(f"::error::{full}: could not check - {e}")
            return 2
        print(f"{full}: {n} layers inspected, {len(problems)} problem(s)")
        for p in problems:
            print(f"::error::{full}: {p}")
        if problems or n == 0:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
