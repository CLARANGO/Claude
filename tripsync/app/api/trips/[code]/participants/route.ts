import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ code: string }> }
) {
  try {
    const { code } = await params;
    const { name, startDate, endDate, budget, departureCity } =
      await request.json();

    if (!name?.trim() || !startDate || !endDate || !budget || !departureCity?.trim()) {
      return NextResponse.json(
        { error: "All fields are required" },
        { status: 400 }
      );
    }

    const start = new Date(startDate);
    const end = new Date(endDate);
    if (end <= start) {
      return NextResponse.json(
        { error: "End date must be after start date" },
        { status: 400 }
      );
    }

    const budgetNum = Number(budget);
    if (isNaN(budgetNum) || budgetNum <= 0 || budgetNum > 500000) {
      return NextResponse.json({ error: "Invalid budget" }, { status: 400 });
    }

    const trip = await prisma.trip.findUnique({
      where: { code: code.toUpperCase() },
    });

    if (!trip) {
      return NextResponse.json({ error: "Trip not found" }, { status: 404 });
    }

    const participant = await prisma.participant.create({
      data: {
        tripId: trip.id,
        name: name.trim(),
        startDate: start,
        endDate: end,
        budget: budgetNum,
        departureCity: departureCity.trim(),
      },
    });

    return NextResponse.json({ participant });
  } catch {
    return NextResponse.json(
      { error: "Failed to add participant" },
      { status: 500 }
    );
  }
}
