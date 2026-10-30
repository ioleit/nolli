// Modules the viewer imports straight from a CDN, typed from their npm packages (dev
// dependencies of the same version). No top-level import here: these must be ambient.

declare module "https://cdn.jsdelivr.net/npm/cytoscape@3.34.4/+esm" {
  import cytoscape = require("cytoscape");
  export default cytoscape;
}

declare module "https://cdn.jsdelivr.net/npm/cytoscape-fcose@2.2.0/+esm" {
  const fcose: import("cytoscape").Ext;
  export default fcose;
}

declare module "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs" {
  const mermaid: {
    initialize(config: object): void;
    render(id: string, source: string): Promise<{ svg: string }>;
  };
  export default mermaid;
}

declare module "https://cdn.jsdelivr.net/npm/libavoid-js@0.5.0-beta.5/dist/index.js" {
  export const AvoidLib: { load(wasmUrl?: string): Promise<void>; getInstance(): any };
}
