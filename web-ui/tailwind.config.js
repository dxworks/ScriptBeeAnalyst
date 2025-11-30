/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{html,ts}",
  ],
  theme: {
    extend: {
      colors: {
        // Primary dark navy for header/sidebar
        'navy': {
          900: '#0f1221',
          800: '#1a1f36',
          700: '#252b48',
        },
        // Accent purple/violet
        'accent': {
          DEFAULT: '#6366f1',
          light: '#818cf8',
          dark: '#4f46e5',
        },
        // Background grays
        'surface': {
          DEFAULT: '#f5f7fa',
          card: '#ffffff',
          hover: '#f0f2f5',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'card': '0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)',
        'card-hover': '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
      },
      borderRadius: {
        'card': '12px',
      },
    },
  },
  plugins: [],
}
