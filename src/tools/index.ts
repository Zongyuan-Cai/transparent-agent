import { Ajv2020 } from 'ajv/dist/2020.js';
import type { ValidateFunction } from 'ajv';
import type { Data, Tool, ToolContext, ToolResult, ToolSpec } from '../types.js';
import { Cancelled, PersistenceError, ToolInterrupted, errorText } from '../types.js';
import { bound } from './base.js';
import { fileTools } from './files.js';
import { searchTools } from './search.js';
import { shellTools } from './shell.js';

export class ToolRegistry {
  private tools = new Map<string, { tool: Tool; validate: ValidateFunction }>();
  private validator = new Ajv2020({ strict: false, strictNumbers: true, allErrors: true });
  constructor(tools: Tool[] = []) { for (const tool of tools) this.register(tool); }
  register(tool: Tool): void {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(tool.name) || this.tools.has(tool.name) || typeof tool.run !== 'function') throw new Error('invalid or duplicate tool');
    const parameters = structuredClone(tool.parameters);
    if (parameters.properties && parameters.additionalProperties === undefined) parameters.additionalProperties = false;
    this.tools.set(tool.name, { tool: { name: tool.name, description: tool.description, parameters, run: tool.run.bind(tool) }, validate: this.validator.compile(parameters) });
  }
  names(): string[] { return [...this.tools.keys()]; }
  specs(): ToolSpec[] { return [...this.tools.values()].map(({ tool }) => structuredClone({ name: tool.name, description: tool.description, parameters: tool.parameters })); }
  async execute(name: string, raw: string, context: ToolContext): Promise<ToolResult> {
    if (context.signal.aborted) throw new Cancelled();
    try {
      const entry = this.tools.get(name); if (!entry) throw new Error(`unknown tool ${name}`);
      const args: unknown = JSON.parse(raw.trim() || '{}');
      if (!args || typeof args !== 'object' || Array.isArray(args) || !entry.validate(args)) throw new Error(`invalid arguments: ${this.validator.errorsText(entry.validate.errors)}`);
      const result = await entry.tool.run(args as Data, context);
      if (!result || typeof result.output !== 'string') throw new Error('tool did not return ToolResult');
      return bound(result, context.max_output_chars);
    } catch (error) {
      if (error instanceof Cancelled || error instanceof ToolInterrupted || error instanceof PersistenceError) throw error;
      return bound({ output: errorText(error), is_error: true }, context.max_output_chars);
    }
  }
}
export const buildRegistry = (): ToolRegistry => new ToolRegistry([...fileTools, ...searchTools, ...shellTools]);
export { bound, workspacePath, truncateMiddle } from './base.js';
