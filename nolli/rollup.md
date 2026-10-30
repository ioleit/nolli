# Roll-up: from facts to a picture

The model stores every dependency at the finest level it could resolve: class `Store` →
class `Echo`, module `main` → module `worlds`, module `App.tsx` → endpoint
`GET /api/worlds`. The picture never shows that level all at once; it shows boxes at
some chosen depth — a package, a module, a class — and the chosen depth can differ from
place to place.

**Roll-up** is what connects the two. For each stored edge, replace both ends by the box
that currently stands for them (the nearest unfolded ancestor), add up the counts, and
drop edges whose two ends fall into the same box. Unfold a box and the same facts roll up
to finer boxes; nothing is re-extracted. `rollup.py` does this; `view.py` picks the
default set of unfolded boxes within a node budget.

Everything else in `rollup.py` is a rule applied during the roll-up to keep the picture
readable. Each was a judgement call on a few repositories (reasons in `NOTES.md`):

- **chain compression**: a directory whose only content is one subdirectory is drawn as
  one box with the joined path;
- **fan bundling**: when one box depends on three or more children of another unfolded
  box, one edge to that box's boundary replaces them, and absorbs any deeper edge to the
  same target;
- **groups are a filter**: a group (external packages, environment, deployment, HTTP
  API) the view shows is a box like any other, folded or unfolded; one it doesn't show
  becomes **badges instead of edges**, as do *utilities*, boxes with more than N
  incoming edges in the picture (N = 6 by default, a viewer option): their incoming edges
  are not drawn; the utility says how many boxes use it ("used by 7"), and an unfolded box
  lists the utilities its children use ("→ progress", bottom right). A folded box lists none. By default only HTTP API is shown;
- **internal edges** (both ends inside the same unfolded box) are hairlines, or hidden
  with a count in the box header;
- **weak edges** (a small share of their source's references) are faint and unlabeled;
- **interfaces** (endpoints, env variables) are derived through when their group is not
  shown: a call and the route that serves it become one edge caller → handler; when the
  group is shown, calls go into it (or the route group or endpoint, once unfolded), and
  `provide` is drawn from it to the module that realises it (the fact keeps UML's
  direction, the picture reverses it);
- **a module's own code** (functions, constants, statements outside any class) is a small
  anchor in the module's header, not a box; a Python package's `__init__` is the
  package's own code in the same way, and its edges into its own package are dropped as
  the default re-exports every package has;
- **images** that run as exactly one service are drawn as that service.

This file's rules exist once, in Python. `nolli/scene.py` applies them to a view state
(unfolded boxes plus options) and decides everything else the picture shows: labels,
badges, emphasis, tooltips. The viewer asks `viewer/serve.py` for that scene on every
change (`/api/scene`) and only lays it out and draws it; `nolli extract` writes the scene
of the default cut as `scene.json`.
