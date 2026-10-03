// Explicitly enable: --extension /absolute/path/examples/event_logger.mjs
export function register(api) {
  api.subscribe({
    emit(event) {
      // The default trace retains full data. This observer displays only identifiers.
      process.stderr.write(`[event ${event.seq}] ${event.type}\n`);
    },
  });
}
