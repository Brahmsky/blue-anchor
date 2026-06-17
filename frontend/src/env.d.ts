/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DESKTOP_API_BASE_URL?: string;
  readonly VITE_PROXY_TARGET?: string;
  readonly VITE_BUILD_OUT_DIR?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
