import { Project } from "../types/project.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("ProjectStore");

export class ProjectStore {
  private project: Project | null = null;

  setProject(project: Project): void {
    this.project = project;
    log.info(`Project set: ${project.id} — ${project.name}`);
  }

  getProject(): Project | null {
    return this.project;
  }

  updateStatus(status: Project["status"]): void {
    if (this.project) {
      this.project.status = status;
      log.info(`Project status → ${status}`);
    }
  }
}
