import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite config for the au Jibun Bank admin dashboard SPA.
// Environment is supplied via VITE_* variables (Cognito + API endpoint),
// injected by the CI/CD Amplify deploy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
