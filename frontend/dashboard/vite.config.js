import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Different default port from frontend/diagnose (5173), so both can
    // run side by side during local development.
    port: 5174,
  },
})
