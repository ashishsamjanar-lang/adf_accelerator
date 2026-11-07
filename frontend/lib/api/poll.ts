/**
 * Poll a queued worker job until it settles. `read` returns the latest state;
 * `done` decides when it's settled. Resolves with the last state seen — callers
 * must handle a still-running result (the worker finishes it out of band).
 */
export async function pollUntil<T>(
  read: () => Promise<T>,
  done: (value: T) => boolean,
  { intervalMs = 1500, timeoutMs = 60_000 }: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!done(value) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    value = await read();
  }
  return value;
}
