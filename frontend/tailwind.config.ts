import type { Config } from "tailwindcss";

/**
 * Tailwind theme mirrors the design tokens in app/globals.css.
 * Colours are a small, semantic set: iris/violet is the single brand/primary hue
 * (the `teal`/`forge` keys are kept as names after the recolour to limit churn);
 * blue / amber / red / green / purple are status hues only.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./features/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#1e1e24", 2: "#3f3f47" },
        muted: "#6b6b74",
        faint: "#9a9aa4",
        bg: { DEFAULT: "#f3f2fb", 2: "#eceaf9" },
        soft: "#efeef9",
        surface: { DEFAULT: "#ffffff", 2: "#f7f7fb", 3: "#efeef9" },
        line: { DEFAULT: "#e4e2f2", 2: "#ececf6" },
        iris: "#6366f1",
        teal: {
          DEFAULT: "#5046e5",
          dark: "#4038c4",
          soft: "#eef0ff",
          50: "#eef0ff",
          100: "#e0e1ff",
          600: "#4a40d8",
          700: "#4038c4",
          800: "#362fa6",
        },
        mint: "#eef0ff",
        // Brand alias — iris/violet family
        forge: { DEFAULT: "#5046e5", dark: "#4038c4", bg: "#eef0ff" },
        blue: { DEFAULT: "#2563eb", bg: "#e5edff" },
        amber: { DEFAULT: "#b45309", bg: "#fdf1dd" },
        red: { DEFAULT: "#dc2626", bg: "#fdeaea" },
        green: { DEFAULT: "#15803d", bg: "#e3f4e9" },
        purple: { DEFAULT: "#7c3aed", bg: "#f2ebfd" },
        lime: "#a3e635",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "Manrope", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["var(--font-mono)", "'DM Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1.4" }],
        xs: ["0.75rem", { lineHeight: "1.5" }],
        sm: ["0.8125rem", { lineHeight: "1.55" }],
        base: ["0.875rem", { lineHeight: "1.5" }],
        lg: ["1rem", { lineHeight: "1.4" }],
        xl: ["1.125rem", { lineHeight: "1.35" }],
        "2xl": ["1.375rem", { lineHeight: "1.3" }],
        "3xl": ["1.75rem", { lineHeight: "1.2" }],
      },
      borderRadius: {
        sm: "6px",
        DEFAULT: "8px",
        md: "8px",
        lg: "12px",
      },
      boxShadow: {
        sm: "0 1px 2px rgba(20,20,20,0.04), 0 1px 3px rgba(20,20,20,0.06)",
        md: "0 2px 4px rgba(20,20,20,0.04), 0 10px 24px rgba(20,20,20,0.08)",
        card: "0 1px 2px rgba(20,20,20,0.04), 0 1px 3px rgba(20,20,20,0.06)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
