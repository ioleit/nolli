// The shapes the viewer works with. The scene is what nolli/logic/formats/viewer.py makes of a projected scene (GET /api/scene).

export interface Chip { id: string; name: string; color: number }

/**
 * What hovering over a box or an edge shows (nolli/logic/formats/descriptions/hover.py): Markdown, one paragraph per
 * sentence, `code spans` for the values in it, a "> " quoted paragraph for a docstring,
 * [problem](verdict:problem) for a verdict's mark (nolli/logic/formats/descriptions/prose.py, rendered by js/md.js).
 */
export type Info = string;

// ---------- review mode (a change: nolli/logic/transformations/merge/, nolli/review/verdicts.py, card.py) ----------

export type Status = "added" | "removed" | "changed";
export type Verdict = "blunder" | "problem" | "question" | "fine" | "good" | "brilliant";

/** A box's change: its own status, and totals in its subtree. */
export interface NodeDiff {
  status: Status;
  added: number; removed: number;  // changed lines
  files: number;  // changed files
  new: number; gone: number;  // entities added / removed
  internal: number;  // changed facts between its own children
  git?: string; old_path?: string; renamed_to?: string;
}

/** One stored fact that changed, with a link to where. */
export interface FactChange {
  fact: string; status: Status; kind: string; subkind: string; src: string; dst: string;
  base: number; head: number; path: string | null; line: number | null; link: string | null;
}

export interface EdgeDiff { status: Status; base: number; head: number; facts: string[]; changes: FactChange[]; more: number }

/** The diff summary of a scene: the two commits and the change as a whole. */
export interface SceneDiff {
  base: string; head: string; base_short: string; head_short: string; remote: string | null; pr: number | null;
  title: string | null; files_changed: number; added: number; removed: number; link: string | null;
  drawn: Partial<Record<Status, number>>; changed_entities: number;
}

/** A numbered review item (GET /api/changes). */
export interface ReviewItem {
  id: string;
  category: "component" | "dependency" | "content";
  status: Status;
  title: string;
  area: string;
  role: string;
  link: string | null;
  entities: string[];
  facts: string[];
  reveal: string[];  // boxes to unfold so it can be seen
  focus: string[];
  classes?: (string | { name: string; added: number; removed: number })[];
  lines?: number;
  added?: number; removed?: number;
  base?: number; head?: number; targets?: string[];
  changes?: FactChange[]; more?: number;  // dependency: the references that changed, with links
  hunks?: { new: [number, number]; old: [number, number]; added: number; removed: number; link: string | null }[]; more_hunks?: number;  // edited file
}

/** A review agent's verdict on a node or edge of the review diagram (review.mmd), resolved. */
export interface ReviewVerdict {
  id: string;  // V1, V2, … in file order
  target: string;  // as written: a diagram node id, or "n_a --> n_b"
  verdict: Verdict;
  reason: string;
  ends: string[];  // what it is about: one entity (a box) or two (an edge); empty if the diagram has no such target
  edge: boolean;
  reveal: string[];  // boxes to unfold so it can be seen
  label: string;  // how to name it: "path" or "a → b"
}

/**
 * Each drawn box and edge comes with what every overlay says about it, under the overlay's
 * name (nolli/logic/formats/viewer.py). The ones the viewer draws are typed here; others (a
 * research overlay) arrive under their own names and show as they are.
 */

/** What the projection decided about a module's own code. */
export interface AnchorProjection {
  container: false;
  utilities_used: string[];  // ids of the utilities whose edges from it became badges
}

/** What the projection decided about a drawn box (nolli/logic/transformations/projection/_core.py). */
export interface BoxProjection {
  container: boolean;  // unfolded with drawn children
  foldable: boolean;  // has children: clicking unfolds (or − folds)
  leaves: number | null;  // folded: how much is inside
  badge: string;
  used_by: number;
  utility: boolean;
  chips: Chip[];
  service_color: number | null;
  utilities_used: string[];  // ids of the utilities whose edges from this box became badges
  internal_changes: number;
}

/** How the picture names a box (nolli/logic/formats/descriptions/label.py). */
export interface Label {
  text: string;
  icons: string[];  // what it is, drawn before the label: "D", "m", "C", "svc", "img", "API", "/", verbs ("GET", "WS")
  prose: string;
}

/** The review's verdicts that land on a box or edge (nolli/review/verdicts.py). */
export interface Verdicts { verdicts: string[]; verdict: Verdict | null }

/** The numbered changes a box or edge shows (nolli/logic/transformations/merge/items.py). */
export interface Items {
  items: string[];  // ids of the numbered changes shown on it (C1, C2, …)
}

/** What a review shows on a box or edge (nolli/review/card.py). */
export interface Review {
  card: Info;  // the card's second part, under a line: what the change did to it, and the verdicts on it
}

interface Overlays {
  label: Label;
  hover: { text: Info };
  change?: NodeDiff | null;  // review mode only
  items?: Items;
  verdicts?: Verdicts;
  review?: Review;
}

/** A module's own code: a small dot inside the module. */
export interface AnchorNode extends Overlays {
  id: string; kind: "anchor"; parent: string | null;
  projection: AnchorProjection;
}

export interface BoxNode extends Overlays {
  id: string;
  kind: string;  // entity kind: directory, file, class, service, image, group, …
  parent: string | null;  // the drawn container, null at the top
  projection: BoxProjection;
}

export type SceneNode = AnchorNode | BoxNode;

/** What the projection decided about a drawn edge. */
export interface EdgeProjection {
  count: number;
  confidence: string;
  via: string[];
  style: "import" | "http" | "env" | "deploy" | "provide";
  local: boolean;
  single: boolean;
  weak: boolean;
  heuristic: boolean;
  types: boolean;  // references only to types: no dependency at run time
  type_refs: number;
  names: Record<string, number>;
  ends: { src: string[]; dst: string[] } | null;  // a lifted line: the drawn nodes it stands for at each end
}

export interface SceneEdge {
  key: string;  // kind|src|dst|subkind, stable across scenes
  kind: string;
  subkind: string;
  src: string;
  dst: string;
  projection: EdgeProjection;
  hover?: { text: Info };
  change?: EdgeDiff | null;  // review mode only
  items?: Items;
  verdicts?: Verdicts;
  review?: Review;
}

export interface ProjectionSpec {
  expanded: string[];
  preset: "default" | "folded" | "changes" | "unfolded" | null;  // the preset that gave expanded; null: unfolded by hand
  roles: string[];
  groups: string[];  // root groups drawn as boxes; the others are badges
  budget: number | null;  // the default cut's budget; null for any other
  utilities: number;  // more incoming edges than this: a utility; 0: off
  max_lines: number;  // lines between two boxes at most (rollup.lift)
  side: "both" | "base" | "head";
}

export interface Scene {
  state: ProjectionSpec;
  nodes: SceneNode[];  // drawing order: a container before what it contains
  edges: SceneEdge[];
  groups: { id: string; namespace: string; name: string; size: number; shown: boolean }[];  // root groups with children, for the Show filter
  roles: { id: string; files: number; dirs: number }[];  // files and directories per role, for the Show filter
  residue: string[];
  stats: { nodes: number; edges: number; ms: { load: number; scene: number; roundtrip?: number } };
  dataset: { key: string; repo: string | null; commit: string | null; files: number | null; entities: number; relations: number };
  url?: string;  // added by the viewer: the request that produced it
  diff?: SceneDiff;  // a diff dataset: the review mode
}

export interface Point { x: number; y: number }
/** Absolute, top-left corner. */
export interface Box { x: number; y: number; w: number; h: number }

/** What the layout returns (layout.js). */
export interface Laid {
  width: number;
  height: number;
  nodes: Map<string, Box>;
  /** mark: the centre of the edge's verdict mark, where ELK placed it as the edge's label */
  edges: { edge: SceneEdge; sections: Point[][]; mark?: Point | null }[];
}

/** Where a dataset's verdicts came from (review.json, nolli/server/reviews.py). */
export interface ReviewOrigin {
  by: "agent" | "upload" | "unknown"; at?: string;
  model?: string; seconds?: number; turns?: number; cost?: number;  // agent
}

/** A review agent run started from the viewer. */
export interface ReviewRun {
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  step: string; steps: string[]; error: string; seconds: number; waiting: number; turns: number; cost: number;
}

/** GET /api/review: the review block's state. */
export interface ReviewStatus {
  verdicts: ReviewOrigin | null;
  run: ReviewRun | null;
  can_run: boolean;
  why_not: "key" | "package" | "repo" | "dataset" | null;
  uploads: boolean;
  model: string;
  server: "view" | "serve";
}
