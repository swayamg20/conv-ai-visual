import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/features/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        /* Semantic tokens (cascade from CSS vars) */
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        "background-elevated": "hsl(var(--background-elevated))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
          start: "hsl(var(--accent-start))",
          end: "hsl(var(--accent-end))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        state: {
          idle: "hsl(var(--state-idle))",
          listening: "hsl(var(--state-listening))",
          processing: "hsl(var(--state-processing))",
          speaking: "hsl(var(--state-speaking))",
        },
        /* Murmur named palette (for direct use: bg-amber, text-chalk, etc.) */
        void: "hsl(var(--void))",
        slate: "hsl(var(--slate))",
        graphite: "hsl(var(--graphite))",
        chalk: {
          DEFAULT: "hsl(var(--chalk))",
          soft: "hsl(var(--chalk-soft))",
          faint: "hsl(var(--chalk-faint))",
        },
        amber: "hsl(var(--amber))",
        lavender: "hsl(var(--lavender))",
        sage: "hsl(var(--sage))",
        ember: "hsl(var(--ember))",
      },
      spacing: {
        '18': '4.5rem',
        '22': '5.5rem',
        '26': '6.5rem',
      },
      fontSize: {
        'hero': ['3.5rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
        'display': ['2.5rem', { lineHeight: '1.2', letterSpacing: '-0.01em' }],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "var(--radius-xl)",
        '2xl': "var(--radius-2xl)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
        handwriting: ["var(--font-handwriting)", "var(--font-handwriting-alt)", "cursive"],
      },
      boxShadow: {
        'glass': 'none',
        'glass-lg': 'none',
        'orb': '0 0 24px rgba(139, 126, 200, 0.3), 0 0 48px rgba(139, 126, 200, 0.12)',
        'orb-amber': '0 0 20px rgba(245, 166, 35, 0.15), 0 0 40px rgba(245, 166, 35, 0.05)',
        'orb-sage': '0 0 30px rgba(107, 203, 119, 0.25)',
        'orb-ember': '0 0 40px rgba(239, 68, 68, 0.5)',
      },
      backdropBlur: {
        'xs': '2px',
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "0.6", transform: "scale(1)" },
          "50%": { opacity: "1", transform: "scale(1.05)" },
        },
        "wave": {
          "0%, 100%": { transform: "scaleY(0.5)" },
          "50%": { transform: "scaleY(1)" },
        },
        "slide-up": {
          "0%": { transform: "translateY(100%)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        "scale-in": {
          "0%": { transform: "scale(0.9)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        "card-enter": {
          "0%": { opacity: "0", transform: "scale(0.95)", filter: "blur(4px)" },
          "100%": { opacity: "1", transform: "scale(1)", filter: "blur(0)" },
        },
        "settle-glow": {
          "0%": { boxShadow: "0 0 20px rgba(245,166,35,0.15), 0 0 40px rgba(245,166,35,0.05)" },
          "100%": { boxShadow: "0 0 0px rgba(245,166,35,0), 0 0 0px rgba(245,166,35,0)" },
        },
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-glow": "pulse-glow 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "wave": "wave 1.5s ease-in-out infinite",
        "slide-up": "slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
        "scale-in": "scale-in 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
        "card-enter": "card-enter 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
        "settle-glow": "settle-glow 2.5s ease-out forwards",
        "fade-in-up": "fade-in-up 0.4s cubic-bezier(0.16, 1, 0.3, 1) both",
      },
    },
  },
  plugins: [],
};
export default config;
