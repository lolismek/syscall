export function buildValidationPrompt(
  taskTitle: string,
  taskInstructions: string,
  interfaceContract: string,
  diff: string
): string {
  return `Review the following code submission from a worker agent.

<task>
Title: ${taskTitle}
Instructions: ${taskInstructions}
Interface Contract: ${interfaceContract}
</task>

<diff>
${diff}
</diff>

Evaluate whether the submission fulfills the task requirements and interface contract.

Output ONLY valid JSON:

{
  "accepted": true or false,
  "feedback": "If rejected: specific, actionable feedback on what to fix. If accepted: brief summary of what was done well.",
  "issues": ["list of specific issues if rejected, empty array if accepted"]
}

Rules:
- Accept if the code reasonably fulfills the task and matches the interface contract
- Reject if there are missing exports, wrong signatures, obvious bugs, or contract violations
- Be pragmatic — don't reject for style issues or minor imperfections
- Output ONLY the JSON, no markdown fences`;
}
