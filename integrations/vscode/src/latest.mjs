export function createLatestOnlyGuard() {
  let sequence = 0;
  const latestByKey = new Map();

  return Object.freeze({
    begin(key, version) {
      sequence += 1;
      const ticket = Object.freeze({ key, version, sequence });
      latestByKey.set(key, sequence);
      return ticket;
    },

    isCurrent(ticket, version) {
      return (
        latestByKey.get(ticket.key) === ticket.sequence &&
        ticket.version === version
      );
    },

    invalidate(key) {
      latestByKey.delete(key);
    },
  });
}
