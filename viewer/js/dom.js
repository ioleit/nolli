// Element lookup that fails loudly: the page's markup is fixed, a missing id is a bug.

/**
 * @template {Element} [T=HTMLElement]
 * @param {string} id
 * @returns {T}
 */
export function byId(id) {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} is missing from the page`);
  return /** @type {T} */ (/** @type {unknown} */ (e));
}

/** @param {string} id */
export const input = id => /** @type {HTMLInputElement} */ (byId(id));
/** @param {string} id */
export const select = id => /** @type {HTMLSelectElement} */ (byId(id));
