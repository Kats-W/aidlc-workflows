/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CHAT_ENDPOINT?: string;
  readonly VITE_DEMO_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
