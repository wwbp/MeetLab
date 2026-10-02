// Prepare for study (console): words for the bot pool's state, and the end time the
// runner expects. The pool itself is agent-runner's capacity.py.

export type CapacityStatus =
  | { available: false; reason: string }
  | {
      available: true;
      min_instances: number;
      ready_instances: number;
      sessions_per_instance: number;
      warm_until: string | null;
      max_instances?: number;
      desired_instances?: number;
      capped?: boolean;
      unhealthy_instances?: number;
    };

export function describeCapacity(s: CapacityStatus): string {
  if (!s.available) return s.reason;
  const stuck = s.unhealthy_instances ?? 0;
  const warning = stuck
    ? ` ${stuck} machine${stuck === 1 ? ' is' : 's are'} stuck shutting down and block the pool; ask an engineer.`
    : '';
  if (s.min_instances === 0 || !s.warm_until) {
    return 'Not prepared: the first bot of a study waits about 2 minutes for a machine.' + warning;
  }
  return (
    `${s.ready_instances} of ${s.min_instances} machines ready, for about ` +
    `${s.min_instances * s.sessions_per_instance} sessions at once, until ${new Date(s.warm_until).toLocaleString()}.` +
    warning
  );
}

/** A `datetime-local` form value (the browser's own zone) as an ISO instant. */
export function untilFromLocal(local: string): string {
  return new Date(local).toISOString();
}
