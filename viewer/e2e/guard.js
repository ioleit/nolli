// `test` for every spec here: it fails when the page throws (an uncaught error or a rejected
// promise) or reports an error in its status line (a layout ELK refused, say), which a person
// might not notice.

import { test as base, expect } from "@playwright/test";

export const test = base.extend({
  page: async ({ page }, use) => {
    /** @type {string[]} */
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await use(page);
    expect(errors, "errors thrown in the page").toEqual([]);
    const status = page.isClosed() ? "" : await page.locator("#status").textContent();
    expect(status, "the status line").not.toMatch(/^error/);
  },
});

export { expect };
