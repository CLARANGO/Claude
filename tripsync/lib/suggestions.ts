import { DESTINATIONS, buildTripOption, TripOption } from "./pricing";

export interface DateRange {
  start: Date;
  end: Date;
}

// Returns the number of full calendar nights in the overlap (end - start in whole days).
// Uses strict inequality so ranges sharing exactly one endpoint count as 0.
export function findOverlapDays(ranges: DateRange[]): number {
  if (ranges.length === 0) return 0;
  const overlapStart = new Date(
    Math.max(...ranges.map((r) => r.start.getTime()))
  );
  const overlapEnd = new Date(Math.min(...ranges.map((r) => r.end.getTime())));
  if (overlapEnd <= overlapStart) return 0;
  return Math.floor(
    (overlapEnd.getTime() - overlapStart.getTime()) / (1000 * 60 * 60 * 24)
  );
}

export function getOverlapRange(
  ranges: DateRange[]
): { start: Date; end: Date } | null {
  if (ranges.length === 0) return null;
  const overlapStart = new Date(
    Math.max(...ranges.map((r) => r.start.getTime()))
  );
  const overlapEnd = new Date(Math.min(...ranges.map((r) => r.end.getTime())));
  if (overlapEnd <= overlapStart) return null;
  return { start: overlapStart, end: overlapEnd };
}

export interface ParticipantInput {
  name: string;
  startDate: Date;
  endDate: Date;
  budget: number;
  departureCity: string;
}

export function generateSuggestions(
  participants: ParticipantInput[]
): TripOption[] {
  if (participants.length === 0) return [];

  const ranges: DateRange[] = participants.map((p) => ({
    start: new Date(p.startDate),
    end: new Date(p.endDate),
  }));

  const nights = findOverlapDays(ranges);
  if (nights < 3) return [];

  const budgets = participants.map((p) => p.budget);
  const departureCities = participants.map((p) => p.departureCity);
  const tripNights = Math.min(nights, 7);

  const options: TripOption[] = DESTINATIONS.map((dest) =>
    buildTripOption(dest, tripNights, budgets, departureCities)
  );

  // Sort: within-budget first, then by total cost ascending
  options.sort((a, b) => {
    if (a.withinBudget && !b.withinBudget) return -1;
    if (!a.withinBudget && b.withinBudget) return 1;
    return a.totalCostPerPerson - b.totalCostPerPerson;
  });

  return options.slice(0, 3);
}
