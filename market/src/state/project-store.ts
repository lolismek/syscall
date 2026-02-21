import fs from "fs/promises";
import path from "path";
import { Project } from "../types/project.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("ProjectStore");

export class ProjectStore {
  private project: Project | null = null;
  private savePath: string | null = null;
  private saveQueued = false;
  private saving = false;

  setSavePath(savePath: string): void {
    this.savePath = savePath;
  }

  private save(): void {
    if (!this.savePath) return;
    if (this.saving) {
      this.saveQueued = true;
      return;
    }
    this._doSave();
  }

  private async _doSave(): Promise<void> {
    if (!this.savePath) return;
    this.saving = true;
    try {
      let data: Record<string, unknown> = {};
      try {
        const raw = await fs.readFile(this.savePath, "utf-8");
        data = JSON.parse(raw);
      } catch {
        // file doesn't exist yet — TaskBoard will create it
      }
      data.project = this.project;
      const dir = path.dirname(this.savePath);
      await fs.mkdir(dir, { recursive: true });
      const tmp = this.savePath + ".tmp";
      await fs.writeFile(tmp, JSON.stringify(data, null, 2));
      await fs.rename(tmp, this.savePath);
    } catch (err) {
      log.warn(`Failed to save project state: ${err}`);
    } finally {
      this.saving = false;
      if (this.saveQueued) {
        this.saveQueued = false;
        this._doSave();
      }
    }
  }

  setProject(project: Project): void {
    this.project = project;
    log.info(`Project set: ${project.id} — ${project.name}`);
    this.save();
  }

  getProject(): Project | null {
    return this.project;
  }

  hydrateProject(project: Project): void {
    project.createdAt = new Date(project.createdAt);
    this.project = project;
    log.info(`Hydrated project: ${project.id} — ${project.name}`);
  }

  updateStatus(status: Project["status"]): void {
    if (this.project) {
      this.project.status = status;
      log.info(`Project status → ${status}`);
      this.save();
    }
  }
}
