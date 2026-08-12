import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Outfit Variable"', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        atlas: {
          primary: '#144e48',
          primaryHover: '#0e3a35',
          accent: '#b1d1c8',
          accentSubtle: '#edf5f3',
          ink: '#0d0d0c',
          body: '#78786f',
          muted: '#a3a39b',
          border: '#ebebea',
          borderHover: '#d8dad8',
          bg: '#f4f5f4',
          card: '#ffffff',
          rowBorder: '#f1f2f1',
          danger: '#ff5b5b',
          dangerHover: '#e64d4d',
          warnBg: '#fdf6e7',
          warnBorder: '#f0e2c0',
          warnIcon: '#8a6a1f',
          warnTextStrong: '#5c4712',
          warnText: '#7a6528',
          diffOldBg: '#fdf0f0',
          diffOldText: '#8a3a3a',
          diffNewBg: '#edf5f3',
          diffNewText: '#144e48',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
