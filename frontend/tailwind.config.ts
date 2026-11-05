import type { Config } from "tailwindcss";

const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: v("page"),
        surface: v("surface"),
        raised: v("raised"),
        line: v("line"),
        ink: { DEFAULT: v("ink"), 2: v("ink-2"), 3: v("ink-3") },
        accent: v("accent"),
        buy: v("buy"),
        sell: v("sell"),
        good: v("good"),
        warn: v("warn"),
        serious: v("serious"),
        critical: v("critical"),
      },
      fontFamily: { sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"], mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"] },
      boxShadow: { card: "0 1px 0 0 rgb(var(--line) / 1), 0 8px 24px -12px rgb(0 0 0 / 0.35)" },
      keyframes: {
        spotlight: { "0%": { opacity: "0", transform: "translate(-72%, -62%) scale(0.5)" }, "100%": { opacity: "1", transform: "translate(-50%,-40%) scale(1)" } },
      },
      animation: { spotlight: "spotlight 2s ease .75s 1 forwards" },
    },
  },
  plugins: [],
} satisfies Config;
