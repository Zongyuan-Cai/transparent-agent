#!/usr/bin/env node
import { main } from '../dist/web/cli.js';
process.exitCode = await main();
