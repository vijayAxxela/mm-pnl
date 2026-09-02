import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5190,
    strictPort: true,
    host: true, // listen on all network interfaces (LAN), not just localhost
  },
  preview: {
    port: 5190,
    strictPort: true,
    host: true,
  },
})
