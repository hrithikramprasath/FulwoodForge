import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

/*
 * Minimal, high-signal lint setup:
 * - @eslint/js + typescript-eslint recommended rules
 * - react-hooks rules (catches wrong useEffect dependencies and hook misuse)
 */
export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat["recommended-latest"],
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // renderer/ runs under NODE, not in a browser — it is the PDF engine, invoked as
    // `node render.mjs` by backend/app/services/export.py. Without this block it
    // inherits no environment at all and every `process.*` reference reports
    // "'process' is not defined", which is why it was quietly excluded from linting
    // rather than fixed. It is ~470 lines of production code that renders every PDF,
    // so leaving it unchecked was the wrong trade: `npm run lint` now covers it too.
    files: ["renderer/**/*.{js,mjs}"],
    languageOptions: {
      globals: globals.node,
    },
  },
);
