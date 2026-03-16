/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // IBM Plex Mono for all data display — overrides Tailwind's font-mono
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        // Barlow Condensed for labels, headings, navigation
        condensed: ['"Barlow Condensed"', 'ui-sans-serif', 'sans-serif'],
      },
      colors: {
        wire: {
          bg: '#0f0f0f',
          amber: '#f59e0b',
          success: '#22c55e',
          failure: '#ef4444',
        },
      },
    },
  },
  plugins: [],
}
