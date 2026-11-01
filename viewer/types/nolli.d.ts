// The shapes the viewer works with. The scene mirrors nolli/scene.py (GET /api/scene).

export interface Chip { id: string; name: string; color: number }

/**
 * What hovering over a box or an edge shows (nolli/info.py): Markdown, one paragraph per
 * sentence, `code spans` for the values in it, a "> " quoted paragraph for a docstring,
 * [problem](verdict:problem) for a verdict's mark (nolli/prose.py, rendered by js/md.js).
 */
export type Info = string;

// ---------- review mode (diff datasets, nolli/review.py) ----------

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

/** GET /api/node: everything about one box in a diff dataset. */
export interface Details {
  id: string; kind: string; name: string; path: string | null; link: string | null;
  diff: NodeDiff | null;
  files: { id: string; path: string; git: string; old_path: string | null; added: number; removed: number; binary: boolean;
           link: string | null; blob: string | null; hunks: { new: [number, number]; old: [number, number]; added: number; removed: number; link: string | null }[];
           more_hunks: number }[];
  more_files: number;
  classes: { id: string; name: string; path: string | null; added: number; removed: number; lines: (number | null)[]; link: string | null }[];
  entities: { id: string; kind: string; name: string; status: Status; path: string | null; link: string | null }[];
  more_entities: number;
  dependencies: FactChange[];
  more_dependencies: number;
}

/** A module's own code: a small dot inside the module. */
export interface AnchorNode {
  id: string; kind: "anchor"; parent: string | null; utilities_used: string[]; info: Info;
  items?: string[]; verdicts?: string[]; verdict?: Verdict | null; review?: Info;  // review mode only, as on a box
}

export interface BoxNode {
  id: string;
  kind: string;  // entity kind: directory, file, class, service, image, group, …
  parent: string | null;  // the drawn container, null at the top
  container: boolean;  // unfolded with drawn children
  label: string;
  icons: string[];  // what it is, drawn before the label: "D", "m", "C", "svc", "img", "API", "/", verbs ("GET", "WS")
  foldable: boolean;  // has children: clicking unfolds (or − folds)
  leaves: number | null;  // folded: how much is inside
  badge: string;
  utility: boolean;
  chips: Chip[];
  service_color: number | null;
  uses: string[];  // unfolded boxes: utilities their children use, one line each at the bottom right
  utilities_used: string[];  // ids of the utilities whose edges from this box became badges
  info: Info;
  diff?: NodeDiff | null;  // review mode only
  items?: string[];  // review items shown on this box
  verdicts?: string[];  // the review's verdicts on it (V ids)
  review?: Info;  // the card's second part, under a line: what the change did to it, and the verdicts on it
  verdict?: Verdict | null;  // the worst of them
}

export type SceneNode = AnchorNode | BoxNode;

export interface SceneEdge {
  key: string;  // kind|src|dst|subkind, stable across scenes
  kind: string;
  subkind: string;
  src: string;
  dst: string;
  count: number;
  confidence: string;
  via: string[];
  style: "import" | "http" | "env" | "deploy" | "provide";
  local: boolean;
  single: boolean;
  weak: boolean;
  heuristic: boolean;
  /** references only to types: no dependency at run time */
  types: boolean;
  info: Info;
  diff?: EdgeDiff | null;
  items?: string[];
  verdicts?: string[];
  review?: Info;
  verdict?: Verdict | null;
}

export interface SceneState {
  expanded: string[];
  preset: "default" | "folded" | "changes" | "unfolded" | null;  // the preset that gave expanded; null: unfolded by hand
  roles: string[];
  groups: string[];  // root groups drawn as boxes; the others are badges
  budget: number;
  utilities: number;  // more incoming edges than this: a utility; 0: off
  side: "both" | "base" | "head";
}

export interface Scene {
  state: SceneState;
  nodes: SceneNode[];  // drawing order: a container before what it contains
  edges: SceneEdge[];
  groups: { id: string; namespace: string; name: string; size: number; shown: boolean }[];  // root groups with children, for the Show filter
  roles: { id: string; files: number; dirs: number }[];  // files and directories per role, for the Show filter
  residue: string[];
  stats: { nodes: number; edges: number; ms: { load: number; scene: number; roundtrip?: number } };
  dataset: { name: string; repo: string | null; commit: string | null; files: number | null; entities: number; relations: number };
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
  edges: { edge: SceneEdge; sections: Point[][] }[];
}
