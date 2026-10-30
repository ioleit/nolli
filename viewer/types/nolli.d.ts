// The shapes the viewer works with. The scene mirrors nolli/scene.py (GET /api/scene).

export interface Chip { id: string; name: string; color: number }

/** A module's own code: a small dot inside the module. */
export interface AnchorNode { id: string; kind: "anchor"; parent: string | null; tip: string }

export interface BoxNode {
  id: string;
  kind: string;  // entity kind: directory, file, class, service, image, group, …
  parent: string | null;  // the drawn container, null at the top
  container: boolean;  // unfolded with drawn children
  label: string;
  icon: string;  // "D", "m", "C" or ""
  foldable: boolean;  // has children: clicking unfolds (or − folds)
  leaves: number | null;  // folded: how much is inside
  badge: string;
  utility: boolean;
  chips: Chip[];
  service_color: number | null;
  internal: number | null;
  tip: string;
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
  label: string;
  local: boolean;
  single: boolean;
  weak: boolean;
  dangling: boolean;
  heuristic: boolean;
  tip: string;
}

export interface SceneState {
  expanded: string[];
  roles: string[];
  groups: string[];  // root groups drawn as boxes; the others are badges
  budget: number;
  through: boolean;
  dangling_use: boolean;
  env_edges: boolean;
  bundle: boolean;
  utilities: boolean;
  hide_internal: boolean;
}

export interface Scene {
  state: SceneState;
  nodes: SceneNode[];  // drawing order: a container before what it contains
  edges: SceneEdge[];
  groups: { id: string; name: string; size: number; shown: boolean }[];  // root groups with children, for the Show filter
  residue: string[];
  stats: { nodes: number; edges: number; ms: { load: number; scene: number; roundtrip?: number } };
  dataset: { name: string; repo: string | null; commit: string | null; files: number | null; entities: number; relations: number };
  url?: string;  // added by the viewer: the request that produced it
}

export interface Point { x: number; y: number }
/** Absolute, top-left corner. */
export interface Box { x: number; y: number; w: number; h: number }

/** What every layout engine returns. */
export interface Laid {
  width: number;
  height: number;
  nodes: Map<string, Box>;
  edges: { edge: SceneEdge; sections: Point[][] }[];
}

export interface LayoutOptions {
  engine?: string;
  direction?: "DOWN" | "RIGHT";
  seed?: string;
  /** the layout shown before this one, for engines that keep the picture stable */
  previous?: Laid;
  /** the node that was unfolded or folded to get here */
  toggled?: string;
}
