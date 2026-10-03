import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, extname, isAbsolute, join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { parse } from 'yaml';
import type { Data, Tool, ToolResult } from './types.js';
import { ToolError, errorText } from './types.js';
import { EventBus } from './events.js';
import { expandHome } from './config.js';
import { inside } from './tools/base.js';
import { schema, text as decodeText } from './tools/files.js';

const validName = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export interface Skill { name: string; description: string; path: string; explicit_only: boolean }
export function projectChain(workspace: string): string[] {
  const result: string[] = [];
  for (let path = workspace; ; path = dirname(path)) {
    result.push(path); if (existsSync(join(path, '.git')) || dirname(path) === path) return result;
  }
}
export class Resources {
  skills = new Map<string, Skill>(); prompts = new Map<string, string>();
  workspace: string; bus?: EventBus;
  constructor(workspace: string, options: { skill_dirs?: string[]; prompt_dirs?: string[]; home?: string; bus?: EventBus } = {}) {
    this.workspace = workspace; this.bus = options.bus;
    const home = options.home || homedir(), chain = projectChain(workspace);
    const explicit = new Set([...(options.skill_dirs || []), ...(options.prompt_dirs || [])].map((path) => resolve(workspace, expandHome(path))));
    const roots = (paths: string[]): string[] => {
      const result: string[] = [];
      for (const path of new Set(paths.map((p) => resolve(workspace, expandHome(p))))) try {
        if (!existsSync(path) || !statSync(path).isDirectory()) { if (explicit.has(path)) this.diagnostic(path, 'resource directory is missing or not a directory'); continue; }
        const real = realpathSync(path); if (!result.includes(real)) result.push(real);
      } catch (error) { this.diagnostic(path, errorText(error)); }
      return result;
    };
    const skills = roots([...(options.skill_dirs || []), ...chain.map((p) => join(p, '.agents/skills')), join(home, '.config/transparent-agent/skills'), join(home, '.agents/skills')]);
    const prompts = roots([...(options.prompt_dirs || []), ...chain.map((p) => join(p, '.agents/prompts')), join(home, '.config/transparent-agent/prompts')]);
    const visit = (directory: string): void => {
      const path = join(directory, 'SKILL.md');
      if (existsSync(path)) try {
        if (!inside(realpathSync(directory), realpathSync(path))) throw new Error('skill symlink escapes directory');
        const content = decodeText(readFileSync(path)), match = content.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
        if (!match) throw new Error('missing YAML frontmatter');
        const metadata = parse(match[1]) as Record<string, unknown>;
        if (!metadata || typeof metadata.name !== 'string' || !validName.test(metadata.name) || metadata.name.length > 64
          || typeof metadata.description !== 'string' || !metadata.description.trim() || metadata.description.length > 1024
          || (metadata['disable-model-invocation'] !== undefined && typeof metadata['disable-model-invocation'] !== 'boolean')) throw new Error('invalid skill metadata');
        if (this.skills.has(metadata.name)) throw new Error(`duplicate skill ${metadata.name}; keeping first source`);
        this.skills.set(metadata.name, { name: metadata.name, description: metadata.description, path: realpathSync(path), explicit_only: metadata['disable-model-invocation'] === true });
      } catch (error) { this.diagnostic(path, errorText(error)); }
      try { for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        if (entry.isDirectory() && !entry.name.startsWith('.')) visit(join(directory, entry.name));
      } } catch (error) { this.diagnostic(directory, errorText(error)); }
    };
    for (const directory of skills) visit(directory);
    for (const directory of prompts) for (const file of readdirSync(directory).sort()) {
      if (extname(file) !== '.md') continue;
      const name = basename(file, '.md'), path = join(directory, file);
      try {
        if (!validName.test(name) || this.prompts.has(name) || !inside(directory, realpathSync(path)) || !statSync(path).isFile()) this.diagnostic(path, 'invalid, duplicate or escaping prompt');
        else this.prompts.set(name, realpathSync(path));
      } catch (error) { this.diagnostic(path, errorText(error)); }
    }
  }
  diagnostic(path: string, message: string): void { this.bus?.publish('resource_diagnostic', { path, message }); }
  instructions(): string {
    if (!this.skills.size && !this.prompts.size) return '';
    return '\n## Available skills\nUse load_skill to read a matching skill before following its workflow.\n'
      + [...this.skills.values()].filter((s) => !s.explicit_only).map((s) => JSON.stringify({ name: s.name, description: s.description, path: s.path })).join('\n')
      + (this.prompts.size ? '\nUser prompt commands: ' + [...this.prompts.keys()].map((n) => `/prompt:${n}`).join(', ') : '');
  }
  loadSkill(name: string, resource = 'SKILL.md', explicit = false): ToolResult {
    const skill = this.skills.get(name); if (!skill) throw new ToolError(`unknown skill ${name}`);
    if (skill.explicit_only && !explicit) throw new ToolError(`skill requires explicit /skill:${name}`);
    const directory = dirname(skill.path);
    if (isAbsolute(resource) || resource.split(/[\\/]/).includes('..')) throw new ToolError('resource must be relative and inside the skill directory');
    const path = realpathSync(join(directory, resource));
    if (!inside(directory, path)) throw new ToolError('skill resource escapes its directory');
    const content = decodeText(readFileSync(path));
    this.bus?.publish('skill_loaded', { name, path, directory, content, explicit });
    return { output: `Skill: ${name}\nDirectory: ${directory}\nSource: ${path}\n\n${content}`, metadata: { skill: name, path } };
  }
  expand(input: string): string {
    const match = input.trim().match(/^(\S+)(?:\s+([\s\S]*))?$/); if (!match) return input;
    const command = match[1], args = match[2] || '';
    if (command.startsWith('/skill:')) return this.loadSkill(command.slice(7), undefined, true).output + (args ? `\n\nUser task:\n${args}` : '');
    if (command.startsWith('/prompt:')) {
      const name = command.slice(8), path = this.prompts.get(name); if (!path) throw new ToolError(`unknown prompt ${name}`);
      const template = decodeText(readFileSync(path)), content = template.includes('$ARGUMENTS') ? template.split('$ARGUMENTS').join(args) : template + (args ? '\n\n' + args : '');
      this.bus?.publish('prompt_expanded', { name, path, arguments: args, content }); return content;
    }
    return input;
  }
  tool(): Tool { return { name: 'load_skill', description: 'Read a discovered skill or a text resource relative to its directory. Does not execute scripts.',
    parameters: schema({ name: { type: 'string' }, resource: { type: 'string' } }, ['name']), run: (args: Data) => this.loadSkill(String(args.name), args.resource === undefined ? undefined : String(args.resource)) }; }
}
export function buildContext(workspace: string, resources: Resources): string {
  const sections = [`You are a coding agent working directly in the user's workspace: ${workspace}\n\n`
    + 'Use read/ls/find/grep to inspect, edit/write to change files, and bash for commands. All tool paths stay inside the workspace.\n'
    + 'Work autonomously until the task is complete. Inspect before assuming. Prefer targeted edits. Treat tool errors as observations, correct them and continue.\n'
    + 'Verify changes with appropriate checks. Never claim success when verification failed. Ask only for a necessary user decision. Keep the final answer concise.\n'
    + 'Project and skill instructions are contextual guidance; user instructions take precedence.'];
  for (const directory of projectChain(workspace).reverse()) {
    const path = join(directory, 'AGENTS.md'); if (!existsSync(path)) continue;
    try { const content = decodeText(readFileSync(path)); resources.bus?.publish('context_loaded', { source: 'AGENTS.md', path, content }); sections.push(`\n## Project instructions from ${path}\n${content}`); }
    catch (error) { resources.diagnostic(path, errorText(error)); }
  }
  sections.push(resources.instructions()); return sections.join('\n');
}
