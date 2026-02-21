export function buildPlanPrompt(projectIdea: string, documentationContext?: string): string {
  const docsSection = documentationContext
    ? `\n<documentation_context>
Real documentation for technologies relevant to this project. Use this to write accurate interface contracts, correct API usage, and realistic task instructions:

${documentationContext}
</documentation_context>\n`
    : "";

  return `A user wants to build the following project:

<project_idea>
${projectIdea}
</project_idea>
${docsSection}

Decompose this into a concrete implementation plan. Output ONLY valid JSON with this exact structure:

{
  "projectName": "short-kebab-case-name",
  "scaffold": [
    { "path": "relative/file/path", "content": "file content" }
  ],
  "tasks": [
    {
      "title": "Short task title",
      "description": "What this task accomplishes",
      "instructions": "Detailed step-by-step instructions for the agent implementing this",
      "filePaths": ["src/file1.ts", "src/file2.ts"],
      "dependencies": [],
      "interfaceContract": "Exact function signatures, types, and behavior this task must produce"
    }
  ]
}

Rules:
- Scaffold should include package.json, tsconfig.json, shared types, and project structure
- Each task should be completable by a single agent in one session
- Tasks should be as independent as possible — minimize dependencies
- Use dependency IDs like "task-001", "task-002" etc. (they'll be assigned in order)
- Interface contracts must be precise enough that agents can work independently
- Include 3-8 tasks for a typical project
- Output ONLY the JSON, no markdown fences, no explanation`;
}
