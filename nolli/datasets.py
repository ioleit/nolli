"""Dataset directories: one model as files, the form the command line reads and writes, apart
from any store.

A store keeps models in a layout of its own, perhaps not in files at all. What leaves nolli as
files (``nolli build -o DIR``) is exported from a store, and what comes in as files (``nolli
view DIR``, ``nolli agent check DIR``) is imported into one first. Both go through the
``ModelStore`` interface only, so they work with any store.

A dataset directory holds:

    key.json  model.json  overlays/<name>.json        a commit's model and its maps
    change.json  base/  head/                         for a change: what it says about itself, and its
                                                      two commits, each as above
    report.json [changes.json review.mmd review.md]   made from the model, for readers without
                                                      nolli (``write_extras``); never imported

``model.json`` and the maps are in their stored forms (``nolli/model/serialized.py``), with each
map's ``about`` (who made it, when), so a review's verdicts keep where they came from.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from nolli.model import serialized
from nolli.model.keys import ChangeKey, ModelKey
from nolli.model.model import Key, Model
from nolli.store import ModelStore

FORMAT = 1  # of key.json and change.json in a dataset directory
DATA = ("key.json", "model.json", "overlays", "change.json", "base", "head")  # a dataset directory's model (export_dataset)
EXTRAS = ("report.json", "changes.json", "review.mmd", "review.md")  # what is made from it (write_extras)

Maps = dict[str, tuple[dict[Key, dict], dict | None]]  # map name -> its values and ``about``


class DatasetError(ValueError):
    """A directory that is no dataset this version of nolli reads."""


def _remove(d: Path, names: tuple[str, ...]) -> None:
    for name in names:
        p = d / name
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)


def is_dataset(path: Path) -> bool:
    """Return whether ``path`` is a dataset directory: one with a ``key.json``."""
    return (Path(path) / "key.json").is_file()


# -- export -------------------------------------------------------------------------------


def _read_maps(store: ModelStore, key: ModelKey) -> Maps:
    """Return every map ``store`` keeps for ``key``, with its ``about``."""
    text = key.format_key()
    return {n: (dict(store.open_maps(n).load(text)), store.open_maps(n).load_about(text)) for n in store.list_map_names(text)}


def _write(d: Path, key: ModelKey, model: Model | None, maps: Maps) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **key.to_dict()}))
    if model is not None:
        (d / "model.json").write_text(json.dumps(serialized.dump_model(model), indent=1))
    (d / "overlays").mkdir(exist_ok=True)
    for name, (values, about) in maps.items():
        (d / "overlays" / f"{name}.json").write_text(json.dumps(serialized.dump_map(key.format_key(), values, about)))


def export_dataset(store: ModelStore, key: ModelKey, out: Path) -> None:
    """Write the model ``store`` keeps under ``key`` to the dataset directory ``out``: for a change,
    what it says about itself, its merged model if one is kept, its maps and its two commits.
    Raise KeyError if the store does not have it. A model written there before goes; the
    directory's other files (the extras, a ``pr.md``) stay."""
    out = Path(out)
    _remove(out, DATA)
    if isinstance(key, ChangeKey):
        about = store.load_change(key)
        if about is None:
            raise KeyError(f"no change {key.format_key()} to export")
        _write(out, key, store.load_model(key), _read_maps(store, key))
        (out / "change.json").write_text(json.dumps({"format": FORMAT, "about": about}, indent=1))
        for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
            export_dataset(store, commit, out / side)
        return
    model = store.load_model(key)
    if model is None:
        raise KeyError(f"no model {key.format_key()} to export")
    _write(out, key, model, _read_maps(store, key))


# -- import -------------------------------------------------------------------------------


def _read_json(f: Path) -> dict:
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError) as exc:
        raise DatasetError(f"{f}: {exc}") from None


def read_key(d: Path) -> ModelKey:
    """Return the key of the dataset directory ``d``. Raise DatasetError for one of another format."""
    j = _read_json(Path(d) / "key.json")
    if j.get("format") != FORMAT:
        raise DatasetError(f"{d} is a dataset of another nolli version; build it again")
    return ModelKey.from_dict(j)


def _read(d: Path, key: ModelKey, model_needed: bool = True) -> tuple[Model | None, Maps]:
    f = d / "model.json"
    try:
        model = serialized.parse_model(_read_json(f), key) if f.is_file() or model_needed else None
        maps = {m.stem: serialized.parse_map(_read_json(m)) for m in sorted((d / "overlays").glob("*.json"))}
    except (serialized.FormatError, KeyError, TypeError) as exc:
        raise DatasetError(f"{d} is a dataset of another nolli version ({exc}); build it again") from None
    return model, maps


def _save_kept(store: ModelStore, key: ModelKey, maps: Maps) -> None:
    """Keep the maps that say where they came from (``about``) as they say it; the others go in
    with the model, as extraction's do."""
    for name, (values, about) in maps.items():
        if about is not None:
            store.open_maps(name).save(key.format_key(), values, about)


def _plain(maps: Maps) -> dict[str, dict[Key, dict]]:
    return {n: values for n, (values, about) in maps.items() if about is None}


def import_dataset(store: ModelStore, d: Path) -> ModelKey:
    """Keep the model of the dataset directory ``d`` in ``store``, in place of what the store kept
    under its key, and return the key. Raise DatasetError for a directory that is no dataset of
    this version, and NotAllowed if the store does not keep models of its repository."""
    d = Path(d)
    key = read_key(d)
    if isinstance(key, ChangeKey):
        about = _read_json(d / "change.json")
        if about.get("format") != FORMAT:
            raise DatasetError(f"{d} is a dataset of another nolli version; build it again")
        sides, kept = {}, {}
        for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
            if read_key(d / side) != commit:
                raise DatasetError(f"{d / side} is not the {side} of {key.format_key()}")
            m, maps = _read(d / side, commit)
            sides[side], kept[commit] = (m, _plain(maps)), maps
        model, maps = _read(d, key, model_needed=False)
        store.save_change(key, about["about"], sides, _plain(maps), model)
        kept[key] = maps
        for k, maps in kept.items():
            _save_kept(store, k, maps)
        return key
    model, maps = _read(d, key)
    store.save_model(key, model, _plain(maps))  # type: ignore[arg-type]  # _read gives a model when it is needed
    _save_kept(store, key, maps)
    return key


# -- what is made from a model, for readers without nolli -----------------------------------


def write_extras(store: ModelStore, key: ModelKey, out: Path, budget: int | None = None) -> dict:
    """Write the files a dataset directory gets beside the model: report.json, and for a change what
    a review agent reads (changes.json, review.mmd, review.md). ``budget`` is the size of the
    default projection when the model does not say it. Extras written there before go. Return
    the report."""
    from nolli import operations
    from nolli.logic import transformations
    from nolli.logic.formats import report as report_format
    from nolli.logic.transformations import merge, projection
    from nolli.logic.transformations.merge import items
    from nolli.review import brief

    scene = operations.open_scene(store, key)
    if scene is None:
        raise KeyError(f"no model {key.format_key()} to describe")
    meta = scene.model.meta
    budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or budget or projection.DEFAULT_BUDGET
    default = transformations.apply(scene, projection.fold(projection.ProjectionSpec(expand=projection.AtMost(budget))))
    report = report_format.make_report(scene, default)
    out = Path(out)
    _remove(out, EXTRAS)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    if merge.is_change(scene):
        (out / "changes.json").write_text(json.dumps(items.list_changes(scene), indent=1))
        mmd = brief.make_review_diagram(scene)
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(brief.make_brief(scene, mmd))
    return report
