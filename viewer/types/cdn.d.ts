// Modules the viewer imports straight from a CDN, typed from their npm packages (dev
// dependencies of the same version). No top-level import here: these must be ambient.

declare module "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs" {
  const mermaid: {
    initialize(config: object): void;
    render(id: string, source: string): Promise<{ svg: string }>;
  };
  export default mermaid;
}
