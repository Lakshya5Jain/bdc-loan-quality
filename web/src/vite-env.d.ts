/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "static" when built by `npm run build:static`; the app then reads /data/*.json. */
  readonly VITE_DATA_MODE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
