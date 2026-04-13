export interface Logger {
  info(message: string, fields?: Record<string, unknown>): void
  warn(message: string, fields?: Record<string, unknown>): void
  error(message: string, fields?: Record<string, unknown>): void
}

function emit(
  level: 'info' | 'warn' | 'error',
  message: string,
  fields: Record<string, unknown> = {}
): void {
  const entry = {
    level,
    message,
    timestamp: new Date().toISOString(),
    ...fields
  }

  const line = JSON.stringify(entry)

  if (level === 'error') {
    console.error(line)
    return
  }

  if (level === 'warn') {
    console.warn(line)
    return
  }

  console.log(line)
}

export const consoleLogger: Logger = {
  info(message, fields) {
    emit('info', message, fields)
  },
  warn(message, fields) {
    emit('warn', message, fields)
  },
  error(message, fields) {
    emit('error', message, fields)
  }
}
