import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";

export default defineConfig(
  { ignores: ["dist/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  js.configs.recommended,
  tseslint.configs.strict,
  pluginVue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parser: vueParser,
      parserOptions: { parser: tseslint.parser, extraFileExtensions: [".vue"], sourceType: "module" },
    },
    rules: { "no-undef": "off", "vue/multi-word-component-names": ["error", { ignores: ["App"] }] },
  },
  {
    files: ["src/**/*.ts", "src/**/*.vue"],
    ignores: ["src/**/*.spec.ts"],
    rules: { "no-restricted-globals": ["error", "process", "Buffer", "require", "__dirname", "__filename"] },
  },
);
