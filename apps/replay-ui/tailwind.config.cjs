/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      boxShadow: {
        glow: '0 0 0 1px rgba(110, 231, 255, 0.14), 0 22px 54px rgba(6, 182, 212, 0.22)'
      },
      colors: {
        canvas: '#08111f',
        panel: '#0d1b2e',
        ink: '#e8f0ff',
        accent: '#70e2ff',
        ember: '#ff8b5d',
        gold: '#ffd166'
      },
      animation: {
        'rise-in': 'riseIn 560ms ease-out both',
        shimmer: 'shimmer 2.4s linear infinite'
      },
      keyframes: {
        riseIn: {
          '0%': { opacity: '0', transform: 'translateY(18px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' }
        }
      }
    }
  },
  plugins: []
}
