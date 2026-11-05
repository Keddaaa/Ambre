// The API's two refusals of a manual cycle (src/ambre/infrastructure/worker_lease.py).
const REFUSALS: Record<string, string> = {
  "a worker is active on this database and runs the cycles itself":
    "Worker actif : il lance déjà les cycles tout seul.",
  "a cycle is already running on this database": "Un cycle est déjà en cours.",
};

/** What the owner reads when a manual cycle fails. */
export function cycleErrorMessage(message: string): string {
  return REFUSALS[message] ?? message;
}
