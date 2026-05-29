import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { generateSuggestions } from "@/lib/suggestions";
import { findOverlapDays, getOverlapRange } from "@/lib/suggestions";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ code: string }> }
) {
  try {
    const { code } = await params;
    const trip = await prisma.trip.findUnique({
      where: { code: code.toUpperCase() },
      include: { participants: true },
    });

    if (!trip) {
      return NextResponse.json({ error: "Trip not found" }, { status: 404 });
    }

    if (trip.participants.length === 0) {
      return NextResponse.json({ suggestions: [], stats: null });
    }

    const participants = trip.participants.map((p) => ({
      name: p.name,
      startDate: new Date(p.startDate),
      endDate: new Date(p.endDate),
      budget: p.budget,
      departureCity: p.departureCity,
    }));

    const ranges = participants.map((p) => ({
      start: new Date(p.startDate),
      end: new Date(p.endDate),
    }));

    const overlapDays = findOverlapDays(ranges);
    const overlapRange = getOverlapRange(ranges);
    const budgets = participants.map((p) => p.budget);

    const stats = {
      overlapDays,
      overlapRange,
      minBudget: Math.min(...budgets),
      maxBudget: Math.max(...budgets),
      avgBudget: Math.round(budgets.reduce((a, b) => a + b, 0) / budgets.length),
      participantCount: participants.length,
    };

    const suggestions = generateSuggestions(participants);

    return NextResponse.json({ suggestions, stats });
  } catch {
    return NextResponse.json(
      { error: "Failed to generate suggestions" },
      { status: 500 }
    );
  }
}
