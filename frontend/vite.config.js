import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `npm run dev` (this file's `server` block) is local development only —
// deliberately a different port (5191) than the Docker-served production
// build (5190, see frontend/nginx.conf), so both can run on the same
// machine at once without colliding. `npm run preview` (the `preview`
// block) is a local sanity-check of the production build, so it uses the
// real production port.
// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5191,
    strictPort: true,
    host: true, // listen on all network interfaces (LAN), not just localhost
  },
  preview: {
    port: 5190,
    strictPort: true,
    host: true,
  },
})
