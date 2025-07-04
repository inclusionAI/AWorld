import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vite.dev/config/
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  plugins: [react()],
  server: {
    proxy: {
      '^/api': {
        target: 'http://127.0.0.1:8000',
        // target: 'http://30.230.162.242:8000',
        changeOrigin: true,
        secure: false,
        ws: true
      }
    }
  }
});
