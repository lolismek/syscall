import { query } from "@anthropic-ai/claude-code";
import { SYSTEM_PROMPT } from "./prompts/system.js";
import { config } from "../utils/config.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Orchestrator");

export async function invokeOrchestrator(prompt: string): Promise<string> {
  log.info("Invoking orchestrator...");
  log.debug("Prompt", { prompt: prompt.slice(0, 200) });

  let result = "";

  const response = query({
    prompt,
    options: {
      customSystemPrompt: SYSTEM_PROMPT,
      model: config.model,
      allowedTools: [],
      maxTurns: 3,
      permissionMode: "bypassPermissions",
      env: {
        ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || "",
        PATH: process.env.PATH || "",
      },
      stderr: (data: string) => {
        log.debug(`SDK: ${data.trim()}`);
      },
    },
  });

  try {
    for await (const message of response) {
      if (message.type === "assistant") {
        // Stream assistant text live
        const content = (message as unknown as { message: { content: Array<{ type: string; text?: string }> } }).message.content;
        for (const block of content) {
          if (block.type === "text" && block.text) {
            process.stdout.write(block.text);
          }
        }
      } else if (message.type === "result") {
        process.stdout.write("\n");
        if (message.subtype === "success") {
          result = message.result;
        } else {
          log.error(`Orchestrator error: ${message.subtype}`, message as unknown as Record<string, unknown>);
        }
      }
    }
  } catch (err) {
    log.error(`SDK query failed: ${err}`);
    throw err;
  }

  log.info("Orchestrator response received", { length: result.length });
  return result;
}
