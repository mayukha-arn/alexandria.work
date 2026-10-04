import type { Config } from "tailwindcss";

// All colours come from CSS variables in globals.css: restyle the whole app by editing that one block.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        panel2: "rgb(var(--panel2) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        mute: "rgb(var(--mute) / <alpha-value>)",
        brand: "rgb(var(--brand) / <alpha-value>)",
        good: "rgb(var(--good) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        bad: "rgb(var(--bad) / <alpha-value>)",
        brand2: "rgb(var(--brand2) / <alpha-value>)",
        rail: "rgb(var(--rail) / <alpha-value>)",
        side: "rgb(var(--side) / <alpha-value>)",
        "side-ink": "rgb(var(--side-ink) / <alpha-value>)",
        "side-mute": "rgb(var(--side-mute) / <alpha-value>)",
        "side-hover": "rgb(var(--side-hover) / <alpha-value>)",
        "side-active": "rgb(var(--side-active) / <alpha-value>)",
      },
      fontFamily: { mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"] },
    },
  },
  plugins: [],
};
export default config;
