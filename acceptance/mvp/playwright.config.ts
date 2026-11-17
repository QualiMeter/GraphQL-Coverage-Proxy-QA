import { defineConfig } from '@playwright/test';
import { join } from 'node:path';

const folder = process.env.MVP_REPORT;
if (!folder) throw new Error('Start with python -m acceptance.mvp.run');
export default defineConfig({
  workers: 1,
  outputDir: join(folder, 'results'),
  reporter: [['list'], ['html', { outputFolder: join(folder, 'html'), open: 'never' }]],
});
