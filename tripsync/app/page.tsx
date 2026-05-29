"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  const [tripName, setTripName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [joinCode, setJoinCode] = useState("");

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/trips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: tripName }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Failed to create trip");
      } else {
        router.push(`/trip/${data.trip.code}`);
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function handleJoin(e: React.FormEvent) {
    e.preventDefault();
    const code = joinCode.trim().toUpperCase();
    if (code.length === 6) {
      router.push(`/trip/${code}/join`);
    }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 py-16">
      {/* Hero */}
      <div className="text-center mb-12">
        <div className="text-5xl mb-4">✈️</div>
        <h1 className="text-4xl sm:text-5xl font-bold text-slate-800 mb-3">
          TripSync
        </h1>
        <p className="text-lg text-slate-500 max-w-md">
          Plan group trips effortlessly. Share a link, sync availability and
          budget, get destination ideas — all in one place.
        </p>
      </div>

      <div className="w-full max-w-md space-y-6">
        {/* Create Trip */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
          <h2 className="text-xl font-semibold text-slate-800 mb-1">
            Create a trip
          </h2>
          <p className="text-sm text-slate-500 mb-4">
            Give your trip a name and get a shareable link for your friends.
          </p>
          <form onSubmit={handleCreate} className="space-y-3">
            <input
              type="text"
              value={tripName}
              onChange={(e) => setTripName(e.target.value)}
              placeholder="e.g. Summer 2025 adventure"
              className="w-full px-4 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800 placeholder:text-slate-400"
              required
              maxLength={80}
            />
            {error && <p className="text-sm text-red-500">{error}</p>}
            <button
              type="submit"
              disabled={loading || !tripName.trim()}
              className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Creating…" : "Create trip →"}
            </button>
          </form>
        </div>

        {/* Divider */}
        <div className="flex items-center gap-3 text-slate-400 text-sm">
          <div className="flex-1 border-t border-slate-200" />
          or join with a code
          <div className="flex-1 border-t border-slate-200" />
        </div>

        {/* Join Trip */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
          <h2 className="text-xl font-semibold text-slate-800 mb-1">
            Join a trip
          </h2>
          <p className="text-sm text-slate-500 mb-4">
            Enter the 6-character code your friend shared with you.
          </p>
          <form onSubmit={handleJoin} className="flex gap-2">
            <input
              type="text"
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
              placeholder="ABC123"
              className="flex-1 px-4 py-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800 placeholder:text-slate-400 font-mono tracking-widest uppercase"
              maxLength={6}
            />
            <button
              type="submit"
              disabled={joinCode.trim().length !== 6}
              className="px-5 py-3 rounded-xl bg-slate-800 text-white font-semibold hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Join
            </button>
          </form>
        </div>
      </div>

      <p className="mt-12 text-xs text-slate-400">
        No account required · Free to use
      </p>
    </main>
  );
}
