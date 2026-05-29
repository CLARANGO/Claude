"use client";

import { useState, useEffect, use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

interface Trip {
  id: string;
  code: string;
  name: string;
}

export default function JoinPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = use(params);
  const router = useRouter();
  const [trip, setTrip] = useState<Trip | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  const [form, setForm] = useState({
    name: "",
    startDate: "",
    endDate: "",
    budget: "",
    departureCity: "",
  });

  useEffect(() => {
    fetch(`/api/trips/${code}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.trip) setTrip(data.trip);
        else setNotFound(true);
      })
      .catch(() => setNotFound(true));
  }, [code]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (form.endDate && form.startDate && form.endDate <= form.startDate) {
      setError("End date must be after start date.");
      return;
    }
    setSubmitting(true);

    try {
      const res = await fetch(`/api/trips/${code}/participants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          startDate: form.startDate,
          endDate: form.endDate,
          budget: Number(form.budget),
          departureCity: form.departureCity,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Failed to submit");
      } else {
        setSubmitted(true);
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function field(key: keyof typeof form, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  if (notFound) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center px-4">
        <div className="text-center">
          <div className="text-5xl mb-4">🗺️</div>
          <h1 className="text-2xl font-bold text-slate-800 mb-2">Trip not found</h1>
          <p className="text-slate-500 mb-6">
            The trip code <span className="font-mono font-semibold">{code}</span> doesn&apos;t exist.
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

  if (submitted) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center px-4">
        <div className="text-center max-w-sm">
          <div className="text-5xl mb-4">🎉</div>
          <h1 className="text-2xl font-bold text-slate-800 mb-2">
            You&apos;re in!
          </h1>
          <p className="text-slate-500 mb-6">
            Your availability has been added to{" "}
            <span className="font-semibold text-slate-700">{trip?.name}</span>.
            Once everyone responds, the trip organizer will see destination
            suggestions.
          </p>
          <button
            onClick={() => router.push(`/trip/${code}`)}
            className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold hover:bg-blue-700 transition-colors"
          >
            View trip dashboard →
          </button>
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
    <main className="min-h-screen px-4 py-12">
      <div className="max-w-lg mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="text-4xl mb-3">✈️</div>
          <p className="text-sm font-medium text-blue-600 mb-1 uppercase tracking-wide">
            You&apos;re invited
          </p>
          <h1 className="text-3xl font-bold text-slate-800">{trip.name}</h1>
          <p className="text-slate-500 mt-2 text-sm">
            Fill in your availability and budget so we can find the best
            destination for everyone.
          </p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Name */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Your name
              </label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => field("name", e.target.value)}
                placeholder="Alex"
                required
                maxLength={60}
                className="w-full px-4 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800 placeholder:text-slate-400"
              />
            </div>

            {/* Dates */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Available from
                </label>
                <input
                  type="date"
                  value={form.startDate}
                  onChange={(e) => field("startDate", e.target.value)}
                  required
                  min={new Date().toISOString().split("T")[0]}
                  className="w-full px-3 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Available until
                </label>
                <input
                  type="date"
                  value={form.endDate}
                  onChange={(e) => field("endDate", e.target.value)}
                  required
                  min={form.startDate || new Date().toISOString().split("T")[0]}
                  className="w-full px-3 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800"
                />
              </div>
            </div>

            {/* Budget */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Total budget (USD)
              </label>
              <div className="relative">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 font-medium">
                  $
                </span>
                <input
                  type="number"
                  value={form.budget}
                  onChange={(e) => field("budget", e.target.value)}
                  placeholder="2000"
                  required
                  min={100}
                  max={50000}
                  className="w-full pl-8 pr-4 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800 placeholder:text-slate-400"
                />
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Include flights, hotel, and spending money
              </p>
            </div>

            {/* Departure City */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Departure city
              </label>
              <input
                type="text"
                value={form.departureCity}
                onChange={(e) => field("departureCity", e.target.value)}
                placeholder="e.g. New York, Tokyo, London"
                required
                maxLength={60}
                className="w-full px-4 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800 placeholder:text-slate-400"
              />
              <p className="text-xs text-slate-400 mt-1">
                The city you&apos;d fly from
              </p>
            </div>

            {error && (
              <p className="text-sm text-red-500 bg-red-50 px-4 py-3 rounded-xl">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? "Submitting…" : "Submit my availability →"}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          Trip code:{" "}
          <span className="font-mono font-semibold tracking-widest">{code}</span>
        </p>
      </div>
    </main>
  );
}
