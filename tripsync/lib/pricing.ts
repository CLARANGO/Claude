export type DistanceZone = "domestic" | "regional" | "international";

export interface Destination {
  city: string;
  country: string;
  emoji: string;
  zone: Record<string, DistanceZone>;
  hotelPerNight: number;
  dailyExpenses: number;
  description: string;
}

export const DESTINATIONS: Destination[] = [
  {
    city: "Tokyo",
    country: "Japan",
    emoji: "🗼",
    zone: {
      default: "international",
      Tokyo: "domestic",
      Osaka: "domestic",
      Seoul: "regional",
      Shanghai: "regional",
      Beijing: "regional",
      Bangkok: "regional",
      Singapore: "regional",
    },
    hotelPerNight: 130,
    dailyExpenses: 90,
    description: "Vibrant city with amazing food, culture, and technology",
  },
  {
    city: "Bali",
    country: "Indonesia",
    emoji: "🌴",
    zone: {
      default: "international",
      Singapore: "regional",
      Bangkok: "regional",
      Kuala_Lumpur: "regional",
      Sydney: "regional",
      Perth: "regional",
      Bali: "domestic",
      Jakarta: "domestic",
    },
    hotelPerNight: 80,
    dailyExpenses: 55,
    description: "Tropical paradise with stunning temples and beaches",
  },
  {
    city: "Paris",
    country: "France",
    emoji: "🗼",
    zone: {
      default: "international",
      London: "regional",
      Amsterdam: "regional",
      Berlin: "regional",
      Madrid: "regional",
      Rome: "regional",
      Barcelona: "regional",
      Brussels: "regional",
      Paris: "domestic",
    },
    hotelPerNight: 180,
    dailyExpenses: 130,
    description: "City of light, romance, art, and world-class cuisine",
  },
  {
    city: "Barcelona",
    country: "Spain",
    emoji: "🏖️",
    zone: {
      default: "international",
      Madrid: "domestic",
      Paris: "regional",
      London: "regional",
      Amsterdam: "regional",
      Rome: "regional",
      Lisbon: "regional",
      Barcelona: "domestic",
    },
    hotelPerNight: 150,
    dailyExpenses: 110,
    description: "Stunning architecture, beaches, tapas, and vibrant nightlife",
  },
  {
    city: "New York",
    country: "USA",
    emoji: "🗽",
    zone: {
      default: "international",
      Boston: "domestic",
      Philadelphia: "domestic",
      Washington_DC: "domestic",
      Chicago: "domestic",
      Miami: "domestic",
      Los_Angeles: "domestic",
      San_Francisco: "domestic",
      New_York: "domestic",
      Toronto: "regional",
      Montreal: "regional",
    },
    hotelPerNight: 200,
    dailyExpenses: 150,
    description: "The city that never sleeps — culture, food, and iconic landmarks",
  },
  {
    city: "Bangkok",
    country: "Thailand",
    emoji: "🛕",
    zone: {
      default: "international",
      Singapore: "regional",
      Kuala_Lumpur: "regional",
      Bali: "regional",
      Tokyo: "regional",
      Seoul: "regional",
      Hong_Kong: "regional",
      Bangkok: "domestic",
      Chiang_Mai: "domestic",
    },
    hotelPerNight: 70,
    dailyExpenses: 50,
    description: "Bustling street food, ornate temples, and vibrant markets",
  },
  {
    city: "Lisbon",
    country: "Portugal",
    emoji: "🏰",
    zone: {
      default: "international",
      Madrid: "regional",
      Barcelona: "regional",
      Paris: "regional",
      London: "regional",
      Lisbon: "domestic",
      Porto: "domestic",
    },
    hotelPerNight: 120,
    dailyExpenses: 90,
    description: "Charming hills, seafood, fado music, and sunny weather",
  },
  {
    city: "Singapore",
    country: "Singapore",
    emoji: "🌆",
    zone: {
      default: "international",
      Kuala_Lumpur: "regional",
      Bangkok: "regional",
      Bali: "regional",
      Hong_Kong: "regional",
      Singapore: "domestic",
    },
    hotelPerNight: 160,
    dailyExpenses: 100,
    description: "Ultra-modern city with stunning gardens and diverse cuisine",
  },
  {
    city: "Rome",
    country: "Italy",
    emoji: "🏛️",
    zone: {
      default: "international",
      Milan: "domestic",
      Florence: "domestic",
      Naples: "domestic",
      Rome: "domestic",
      Paris: "regional",
      Barcelona: "regional",
      Madrid: "regional",
      London: "regional",
      Berlin: "regional",
    },
    hotelPerNight: 155,
    dailyExpenses: 115,
    description: "Eternal city of ancient history, art, and incredible food",
  },
  {
    city: "Kyoto",
    country: "Japan",
    emoji: "⛩️",
    zone: {
      default: "international",
      Tokyo: "domestic",
      Osaka: "domestic",
      Kyoto: "domestic",
      Seoul: "regional",
      Shanghai: "regional",
      Beijing: "regional",
      Bangkok: "regional",
      Singapore: "regional",
    },
    hotelPerNight: 120,
    dailyExpenses: 85,
    description: "Ancient temples, geisha culture, and stunning bamboo forests",
  },
];

const FLIGHT_COSTS: Record<DistanceZone, { min: number; max: number }> = {
  domestic: { min: 100, max: 350 },
  regional: { min: 300, max: 700 },
  international: { min: 450, max: 1100 },
};

function normalizeCity(city: string): string {
  return city.trim().replace(/\s+/g, "_");
}

export function estimateFlightCost(
  departureCity: string,
  destination: Destination
): number {
  const key = normalizeCity(departureCity);
  const zone: DistanceZone = destination.zone[key] ?? destination.zone.default;
  const { min, max } = FLIGHT_COSTS[zone];
  // Deterministic midpoint for consistent estimates
  return Math.round((min + max) / 2);
}

export interface TripOption {
  destination: Destination;
  nights: number;
  flightCostPerPerson: number; // average across departure cities (display only)
  maxFlightCostPerPerson: number; // highest individual flight cost (used for budget check)
  hotelCostPerPerson: number;
  dailyExpensesPerPerson: number;
  totalCostPerPerson: number; // based on avg flight for display
  withinBudget: boolean; // true only if the most expensive traveler can still afford it
  departureCities: string[];
}

export function buildTripOption(
  destination: Destination,
  nights: number,
  participantBudgets: number[],
  departureCities: string[]
): TripOption {
  const minBudget = Math.min(...participantBudgets);

  const flightCosts = departureCities.map((city) =>
    estimateFlightCost(city, destination)
  );
  const flightCostPerPerson = Math.round(
    flightCosts.reduce((a, b) => a + b, 0) / flightCosts.length
  );
  const maxFlightCostPerPerson = Math.max(...flightCosts);

  const hotelCostPerPerson = destination.hotelPerNight * nights;
  const dailyExpensesPerPerson = destination.dailyExpenses * nights;
  const totalCostPerPerson =
    flightCostPerPerson + hotelCostPerPerson + dailyExpensesPerPerson;

  // Use max flight cost to conservatively determine affordability:
  // if even the most expensive flier can stay within budget it's truly affordable.
  const worstCaseTotal =
    maxFlightCostPerPerson + hotelCostPerPerson + dailyExpensesPerPerson;

  return {
    destination,
    nights,
    flightCostPerPerson,
    maxFlightCostPerPerson,
    hotelCostPerPerson,
    dailyExpensesPerPerson,
    totalCostPerPerson,
    withinBudget: worstCaseTotal <= minBudget,
    departureCities,
  };
}
