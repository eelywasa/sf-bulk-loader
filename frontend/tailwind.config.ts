import type { Config } from 'tailwindcss'

export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#0070d2',
          dark: '#005fb2',
          light: '#e8f4fd',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
