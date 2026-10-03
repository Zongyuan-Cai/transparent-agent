import { cpSync, mkdirSync } from 'node:fs';
mkdirSync('dist/web/static', { recursive: true });
cpSync('web/static', 'dist/web/static', { recursive: true });
