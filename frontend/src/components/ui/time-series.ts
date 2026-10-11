/** Hold the last observation to the server's right edge, with room for a flat line. */
export function holdUntil<T extends { at: number }>(observations: readonly T[], until: string) {
  const points: T[] = [];
  for (const point of observations) {
    if (!Number.isFinite(point.at)) continue;
    points.push({ ...point, at: Math.max(point.at, points.at(-1)?.at ?? point.at) });
  }
  const first = points[0];
  const last = points.at(-1);
  if (first === undefined || last === undefined) return null;
  const edge = Date.parse(until);
  const end = Math.max(Number.isNaN(edge) ? last.at : edge, last.at, first.at + 60_000);
  if (end > last.at) points.push({ ...last, at: end });
  return { points, start: first.at, end };
}
