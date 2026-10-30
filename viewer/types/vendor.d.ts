// Mermaid, a global set by vendor/mermaid.min.js once js/mermaid.js has loaded it. The
// ES-module libraries in vendor/ have their types next to them (*.d.mts).

declare var mermaid: {
  initialize(config: object): void;
  render(id: string, source: string): Promise<{ svg: string }>;
} | undefined;
