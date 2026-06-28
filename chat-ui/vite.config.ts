import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite config for the au Jibun Bank customer chat SPA.
// The chat endpoint + demo key are supplied via VITE_* variables (see
// .env.example), pointing at the U-08 chat-api Function URL.
export default defineConfig({
  plugins: [react()],
  server: { port: 5174 },
  build: { outDir: 'dist', sourcemap: false },
});
