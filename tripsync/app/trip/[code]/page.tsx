"use client";

import { useState, useEffect, useCallback, use } from "react";
import Link from "next/link";
import type { TripOption } from "@/lib/pricing";

interface Participant {
  id: string;
  name: string;
  startDate: string;
  endDate: string;
  budget: number;
  departureCity: string;
}

interface Trip {
  id: string;
  code: string;
  name: string;
  participants: Participant[];
}

interface Stats {
  overlapDays: number;
  overlapRange: { start: string; end: string } | null;
  minBudget: number;
  maxBudget: number;
  avgBudget: number;
  participantCount: number;
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatCurrency(amount: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(amount);
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function handleCopy() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button
      onClick={handleCopy}
      className="text-xs px-3 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors font-medium"
    >
      {copied ? "✓ Copied" : "Copy link"}
    </button>
  );
}

function TripOptionCard({
  option,
  rank,
}: {
  option: TripOption;
  rank: number;
}) {
  return (
    <div
      className={`bg-white rounded-2xl border p-5 ${
        option.withinBudget
          ? "border-green-200 shadow-green-50 shadow-md"
          : "border-slate-200 shadow-sm"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-2xl">{option.destination.emoji}</span>
            <div>
              <h3 className="font-bold text-slate-800 text-lg leading-tight">
                {option.destination.city}
              </h3>
              <p className="text-xs text-slate-500">{option.destination.country}</p>
            </div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {option.withinBudget ? (
            <span className="text-xs font-semibold text-green-700 bg-green-100 px-2 py-0.5 rounded-full">
              Within budget ✓
            </span>
          ) : (
            <span className="text-xs font-semibold text-orange-600 bg-orange-50 px-2 py-0.5 rounded-full">
              Over budget
            </span>
          )}
          <span className="text-xs text-slate-400">Option #{rank}</span>
        </div>
      </div>

      <p className="text-sm text-slate-500 mb-4">{option.destination.description}</p>

      {/* Cost breakdown */}
      <div className="bg-slate-50 rounded-xl p-4 space-y-2 mb-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
          Cost per person · {option.nights} nights
        </p>
        <div className="flex justify-between text-sm">
          <span className="text-slate-600">✈️ Flight (avg)</span>
          <span className="font-medium text-slate-800">
            {formatCurrency(option.flightCostPerPerson)}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-600">🏨 Hotel</span>
          <span className="font-medium text-slate-800">
            {formatCurrency(option.hotelCostPerPerson)}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-600">🍜 Daily expenses</span>
          <span className="font-medium text-slate-800">
            {formatCurrency(option.dailyExpensesPerPerson)}
          </span>
        </div>
        <div className="border-t border-slate-200 pt-2 flex justify-between">
          <span className="font-semibold text-slate-700">Total</span>
          <span className="font-bold text-slate-900 text-base">
            {formatCurrency(option.totalCostPerPerson)}
          </span>
        </div>
      </div>

      <p className="text-xs text-slate-400">
        Departure cities: {option.departureCities.join(", ")}
      </p>
    </div>
  );
}

export default function TripDashboard({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = use(params);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [suggestions, setSuggestions] = useState<TripOption[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [activeTab, setActiveTab] = useState<"responses" | "suggestions">(
    "responses"
  );

  const shareUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/trip/${code}/join`
      : `/trip/${code}/join`;

  const fetchTrip = useCallback(async () => {
    try {
      const res = await fetch(`/api/trips/${code}`);
      const data = await res.json();
      if (data.trip) setTrip(data.trip);
      else setNotFound(true);
    } catch {
      setNotFound(true);
    }
  }, [code]);

  const fetchSuggestions = useCallback(async () => {
    setLoadingSuggestions(true);
    try {
      const res = await fetch(`/api/trips/${code}/suggestions`);
      const data = await res.json();
      setSuggestions(data.suggestions || []);
      setStats(data.stats || null);
    } catch {
      setSuggestions([]);
    } finally {
      setLoadingSuggestions(false);
    }
  }, [code]);

  useEffect(() => {
    fetchTrip();
  }, [fetchTrip]);

  useEffect(() => {
    if (activeTab === "suggestions" && trip && trip.participants.length > 0) {
      fetchSuggestions();
    }
  }, [activeTab, trip, fetchSuggestions]);

  if (notFound) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center px-4">
        <div className="text-center">
          <div className="text-5xl mb-4">🗺️</div>
          <h1 className="text-2xl font-bold text-slate-800 mb-2">
            Trip not found
          </h1>
          <p className="text-slate-500 mb-6">
            Code{" "}
            <span className="font-mono font-semibold">{code}</span> doesn&apos;t
            exist.
          </p>
          <Link
            href="/"
            className="inline-block px-6 py-3 rounded-xl bg-blue-600 text-white font-semibold hover:bg-blue-700 transition-colors"
          >
            Go home
          </Link>
        </div>
      </main>
    );
  }

  if (!trip) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="text-slate-400 animate-pulse">Loading…</div>
      </main>
    );
  }

  return (
    <main className="min-h-screen px-4 py-8">
      <div className="max-w-2xl mx-auto">
        {/* Top nav */}
        <div className="flex items-center justify-between mb-6">
          <Link
            href="/"
            className="text-sm text-slate-500 hover:text-slate-700 transition-colors"
          >
            ← Home
          </Link>
          <span className="text-xs font-mono font-semibold tracking-widest text-slate-400 bg-slate-100 px-3 py-1 rounded-full">
            {code}
          </span>
        </div>

        {/* Trip header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-slate-800">{trip.name}</h1>
          <p className="text-slate-500 mt-1">
            {trip.participants.length} response
            {trip.participants.length !== 1 ? "s" : ""}
          </p>
        </div>

        {/* Share bar */}
        <div className="bg-blue-50 border border-blue-100 rounded-2xl px-4 py-3 mb-6 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-blue-700 mb-0.5">
              Share with friends
            </p>
            <p className="text-sm text-blue-600 truncate font-mono">{shareUrl}</p>
          </div>
          <CopyButton text={shareUrl} />
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-slate-100 p-1 rounded-xl mb-6">
          <button
            onClick={() => setActiveTab("responses")}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === "responses"
                ? "bg-white text-slate-800 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Responses ({trip.participants.length})
          </button>
          <button
            onClick={() => setActiveTab("suggestions")}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === "suggestions"
                ? "bg-white text-slate-800 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            Suggestions ✨
          </button>
        </div>

        {/* Responses tab */}
        {activeTab === "responses" && (
          <div className="space-y-4">
            {trip.participants.length === 0 ? (
              <div className="text-center py-16 text-slate-400">
                <div className="text-4xl mb-3">👋</div>
                <p className="font-medium">No responses yet</p>
                <p className="text-sm mt-1">
                  Share the link above with your friends
                </p>
              </div>
            ) : (
              <>
                {trip.participants.map((p) => (
                  <div
                    key={p.id}
                    className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-full bg-blue-100 text-blue-700 font-semibold flex items-center justify-center text-sm">
                          {p.name.charAt(0).toUpperCase()}
                        </div>
                        <span className="font-semibold text-slate-800">
                          {p.name}
                        </span>
                      </div>
                      <span className="text-sm font-semibold text-green-700">
                        {formatCurrency(p.budget)}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <p className="text-slate-400 text-xs mb-0.5">
                          Available
                        </p>
                        <p className="text-slate-700">
                          {formatDate(p.startDate)} – {formatDate(p.endDate)}
                        </p>
                      </div>
                      <div>
                        <p className="text-slate-400 text-xs mb-0.5">
                          Flying from
                        </p>
                        <p className="text-slate-700">✈️ {p.departureCity}</p>
                      </div>
                    </div>
                  </div>
                ))}

                <div className="text-center pt-2">
                  <button
                    onClick={fetchTrip}
                    className="text-sm text-blue-600 hover:text-blue-700"
                  >
                    ↻ Refresh
                  </button>
                </div>
              </>
            )}

            {trip.participants.length > 0 && (
              <div className="pt-4">
                <button
                  onClick={() => setActiveTab("suggestions")}
                  className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold hover:bg-blue-700 transition-colors"
                >
                  See trip suggestions ✨
                </button>
              </div>
            )}
          </div>
        )}

        {/* Suggestions tab */}
        {activeTab === "suggestions" && (
          <div>
            {trip.participants.length === 0 ? (
              <div className="text-center py-16 text-slate-400">
                <div className="text-4xl mb-3">🌍</div>
                <p className="font-medium">No responses yet</p>
                <p className="text-sm mt-1">
                  Collect at least one response to see suggestions
                </p>
              </div>
            ) : loadingSuggestions ? (
              <div className="text-center py-16 text-slate-400 animate-pulse">
                Generating suggestions…
              </div>
            ) : (
              <div className="space-y-5">
                {/* Stats banner */}
                {stats && (
                  <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
                      Group summary
                    </p>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <p className="text-xs text-slate-400">Overlap</p>
                        <p className="font-bold text-slate-800">
                          {stats.overlapDays > 0
                            ? `${stats.overlapDays} days`
                            : "None"}
                        </p>
                        {stats.overlapRange && (
                          <p className="text-xs text-slate-500">
                            {formatDate(stats.overlapRange.start)} –{" "}
                            {formatDate(stats.overlapRange.end)}
                          </p>
                        )}
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Min budget</p>
                        <p className="font-bold text-slate-800">
                          {formatCurrency(stats.minBudget)}
                        </p>
                        <p className="text-xs text-slate-500">
                          avg {formatCurrency(stats.avgBudget)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">People</p>
                        <p className="font-bold text-slate-800">
                          {stats.participantCount}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {suggestions.length === 0 ? (
                  <div className="text-center py-10 text-slate-400">
                    <div className="text-4xl mb-3">😕</div>
                    <p className="font-medium">No matching destinations</p>
                    <p className="text-sm mt-1 max-w-xs mx-auto">
                      {stats && stats.overlapDays < 3
                        ? "There's less than 3 days of overlapping availability. Ask everyone to update their dates."
                        : "Try increasing budgets or extending availability windows."}
                    </p>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-slate-500">
                      Based on{" "}
                      <span className="font-medium text-slate-700">
                        {stats?.overlapDays} days
                      </span>{" "}
                      of overlap and a min budget of{" "}
                      <span className="font-medium text-slate-700">
                        {stats && formatCurrency(stats.minBudget)}
                      </span>
                      :
                    </p>
                    {suggestions.map((opt, i) => (
                      <TripOptionCard key={opt.destination.city} option={opt} rank={i + 1} />
                    ))}
                    <p className="text-xs text-slate-400 text-center pt-2">
                      Estimates are indicative only. Actual prices vary.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
