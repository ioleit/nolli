"""Every dependency of the command line and the server that skips datasets, build and scenes:
python dev/examples/layer_skips.py <nolli checkout>

An example of nolli as a library that draws nothing. It opens a scene of the files as they
are and reads its edges directly, each with the references the extractors found behind it. nolli/model/ is
left out: its types are used across all layers."""

import sys
from collections import defaultdict
from pathlib import Path

from nolli import build, scenes
from nolli.model import vocabulary
from nolli.model.schema import RelationKind

TOP = ("nolli/cli.py", "nolli/server/")
MIDDLE = ("nolli/datasets.py", "nolli/build.py", "nolli/scenes.py")
EVERYWHERE = ("nolli/model/",)

repo = Path(sys.argv[1])
scene = scenes.open_built(*build.extract(repo))


def find_file(x: str) -> str | None:
    """Return the file a node of the repository is in, or None for a node outside the repository."""
    return x.file.path if x.is_repo else None


def is_skipped_to(path: str | None) -> bool:
    return bool(path and path.startswith("nolli/") and not path.startswith(TOP + MIDDLE + EVERYWHERE))


found = defaultdict(lambda: defaultdict(set))  # file above -> file below -> {(name, line)}
for e in scene.model.list_edges([RelationKind.USE]):
    src, dst = find_file(e.src), find_file(e.dst)
    if src and src.startswith(TOP) and is_skipped_to(dst):
        for r in vocabulary.read_references(scene.find_element(e)):
            found[src][dst].add((r.get("name") or f"(the module, {e.subkind})", r.get("line")))

for src, dsts in sorted(found.items()):
    print(src)
    for dst, refs in sorted(dsts.items()):
        names = sorted({n for n, _ in refs})
        print(f"  -> {dst}: {', '.join(names)}")
