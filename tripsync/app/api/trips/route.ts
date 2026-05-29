import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

function generateCode(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let code = "";
  for (let i = 0; i < 6; i++) {
    code += chars[Math.floor(Math.random() * chars.length)];
  }
  return code;
}

export async function POST(request: Request) {
  try {
    const { name } = await request.json();
    if (!name?.trim()) {
      return NextResponse.json({ error: "Trip name is required" }, { status: 400 });
    }

    let code = generateCode();
    // Retry on collision (astronomically rare)
    let attempts = 0;
    while (attempts < 5) {
      const existing = await prisma.trip.findUnique({ where: { code } });
      if (!existing) break;
      code = generateCode();
      attempts++;
    }

    const trip = await prisma.trip.create({
      data: { name: name.trim(), code },
    });

    return NextResponse.json({ trip });
  } catch {
    return NextResponse.json({ error: "Failed to create trip" }, { status: 500 });
  }
}
