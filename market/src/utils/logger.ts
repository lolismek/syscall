type LogLevel = "debug" | "info" | "warn" | "error";

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

const currentLevel: LogLevel = (process.env.LOG_LEVEL as LogLevel) || "info";

function shouldLog(level: LogLevel): boolean {
  return LEVEL_PRIORITY[level] >= LEVEL_PRIORITY[currentLevel];
}

function formatMessage(level: LogLevel, component: string, message: string, data?: Record<string, unknown>): string {
  const timestamp = new Date().toISOString();
  const base = `[${timestamp}] [${level.toUpperCase()}] [${component}] ${message}`;
  if (data) {
    return `${base} ${JSON.stringify(data)}`;
  }
  return base;
}

export function createLogger(component: string) {
  return {
    debug: (msg: string, data?: Record<string, unknown>) => {
      if (shouldLog("debug")) console.debug(formatMessage("debug", component, msg, data));
    },
    info: (msg: string, data?: Record<string, unknown>) => {
      if (shouldLog("info")) console.log(formatMessage("info", component, msg, data));
    },
    warn: (msg: string, data?: Record<string, unknown>) => {
      if (shouldLog("warn")) console.warn(formatMessage("warn", component, msg, data));
    },
    error: (msg: string, data?: Record<string, unknown>) => {
      if (shouldLog("error")) console.error(formatMessage("error", component, msg, data));
    },
  };
}
