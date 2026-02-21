import { execFile } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import path from "path";
import { createLogger } from "../utils/logger.js";

const exec = promisify(execFile);
const log = createLogger("Git");

export class GitRepo {
  private lockPromise: Promise<void> = Promise.resolve();

  constructor(private repoPath: string) {}

  private async git(...args: string[]): Promise<string> {
    log.debug(`git ${args.join(" ")}`);
    const { stdout } = await exec("git", args, { cwd: this.repoPath });
    return stdout.trim();
  }

  /** Run a callback while holding an exclusive lock on mutating git ops */
  private async withLock<T>(fn: () => Promise<T>): Promise<T> {
    let release: () => void;
    const next = new Promise<void>((resolve) => { release = resolve; });
    const prev = this.lockPromise;
    this.lockPromise = next;
    await prev;
    try {
      return await fn();
    } finally {
      release!();
    }
  }

  async initRepo(): Promise<void> {
    await fs.rm(this.repoPath, { recursive: true, force: true });
    await fs.mkdir(this.repoPath, { recursive: true });
    await this.git("init");
    // Allow clones to push back to this repo
    await this.git("config", "receive.denyCurrentBranch", "updateInstead");
    // Create initial commit so main branch exists
    const readmePath = path.join(this.repoPath, "README.md");
    await fs.writeFile(readmePath, "# Project\n\nManaged by Syscall orchestrator.\n");
    await this.git("add", ".");
    await this.git("commit", "-m", "Initial commit");
    log.info(`Initialized repo at ${this.repoPath}`);
  }

  async createBranch(branchName: string): Promise<void> {
    await this.withLock(async () => {
      await this.git("branch", branchName);
    });
    log.info(`Created branch: ${branchName}`);
  }

  async writeFile(filePath: string, content: string): Promise<void> {
    const fullPath = path.join(this.repoPath, filePath);
    await fs.mkdir(path.dirname(fullPath), { recursive: true });
    await fs.writeFile(fullPath, content);
  }

  async commitOnMain(message: string): Promise<void> {
    await this.withLock(async () => {
      await this.git("add", ".");
      await this.git("commit", "-m", message);
    });
  }

  async mergeBranch(branchName: string): Promise<string> {
    return this.withLock(async () => {
      const output = await this.git("merge", branchName, "--no-ff", "-m", `Merge ${branchName}`);
      log.info(`Merged branch: ${branchName}`);
      return output;
    });
  }

  async getDiff(branchName: string): Promise<string> {
    return await this.git("diff", "main..." + branchName);
  }

  async getDiffMergeBase(branchName: string, filePaths?: string[]): Promise<string> {
    const mergeBase = await this.git("merge-base", "main", branchName);
    if (filePaths && filePaths.length > 0) {
      return await this.git("diff", mergeBase + ".." + branchName, "--", ...filePaths);
    }
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
