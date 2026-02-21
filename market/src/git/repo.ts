import { execFile } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import path from "path";
import { createLogger } from "../utils/logger.js";

const exec = promisify(execFile);
const log = createLogger("Git");

export class GitRepo {
  constructor(private repoPath: string) {}

  private async git(...args: string[]): Promise<string> {
    log.debug(`git ${args.join(" ")}`);
    const { stdout } = await exec("git", args, { cwd: this.repoPath });
    return stdout.trim();
  }

  async initRepo(): Promise<void> {
    // Wipe any previous workspace so each run starts clean
    await fs.rm(this.repoPath, { recursive: true, force: true });
    await fs.mkdir(this.repoPath, { recursive: true });
    await this.git("init");
    // Create initial commit so main branch exists
    const readmePath = path.join(this.repoPath, "README.md");
    await fs.writeFile(readmePath, "# Project\n\nManaged by Syscall orchestrator.\n");
    await this.git("add", ".");
    await this.git("commit", "-m", "Initial commit");
    log.info(`Initialized repo at ${this.repoPath}`);
  }

  async createBranch(branchName: string): Promise<void> {
    await this.git("checkout", "-b", branchName);
    await this.git("checkout", "main");
    log.info(`Created branch: ${branchName}`);
  }

  async writeFile(filePath: string, content: string): Promise<void> {
    const fullPath = path.join(this.repoPath, filePath);
    await fs.mkdir(path.dirname(fullPath), { recursive: true });
    await fs.writeFile(fullPath, content);
  }

  async commitOnBranch(branch: string, message: string, files: string[]): Promise<void> {
    await this.git("checkout", branch);
    await this.git("add", ...files);
    await this.git("commit", "-m", message);
    await this.git("checkout", "main");
  }

  async commitOnMain(message: string): Promise<void> {
    await this.git("add", ".");
    await this.git("commit", "-m", message);
  }

  async mergeBranch(branchName: string): Promise<string> {
    const output = await this.git("merge", branchName, "--no-ff", "-m", `Merge ${branchName}`);
    log.info(`Merged branch: ${branchName}`);
    return output;
  }

  async getDiff(branchName: string): Promise<string> {
    return await this.git("diff", "main..." + branchName);
  }

  async getDiffMergeBase(branchName: string): Promise<string> {
    // Diff against the point where the branch diverged from main,
    // so the diff is correct even if main has moved forward
    const mergeBase = await this.git("merge-base", "main", branchName);
    return await this.git("diff", mergeBase + ".." + branchName);
  }

  async readFileFromMain(filePath: string): Promise<string> {
    try {
      return await this.git("show", `main:${filePath}`);
    } catch {
      return "";
    }
  }

  async listFiles(): Promise<string[]> {
    const output = await this.git("ls-tree", "-r", "--name-only", "HEAD");
    return output.split("\n").filter(Boolean);
  }

  getRepoPath(): string {
    return this.repoPath;
  }
}
